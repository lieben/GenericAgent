#!/usr/bin/env python3
"""Session inject adapter for Rust WeChat multi-chat.

Purpose
-------
把目标会话的新消息按时间线注入到绑定的主 session（默认 `xinghui:main`）。
- 群聊：把近 N 条上下文消息结构化注入，让主 session 看到完整上下文而不是孤立触发消息。
- 私聊：把新消息作为主 session 的普通用户输入注入。
- 本 adapter 的正式状态落在 `plugins/local_frontends/runtime/state/wechat_session_state.json`，
  与 clawbot / 微信私聊共享同一个主 session；GA root `temp/` 只保留兼容诊断，不作为正式状态源。

接口
----
stdin JSON: {
  "agent":   {"display_name": str, "session_key": str, ...},
  "target":  TargetConfig,
  "prompt":  str,              # Rust core 已经拼好的展示性 prompt
  "session_key": str           # 目标自己的 session_key（不是主 session）
}
stdout JSON: {
  "injected": true|false,
  "main_session_key": str,
  "source": str,
  "items":  [ {role, text, sender, target_id} ],
  "context_file": str,
  "adapter_error": str         # 可选
}

设计要点
------
- 该adapter 禁止把 summary/prompt/隐私写回微信正文。
- 该 adapter 只做"注入"，不调用大模型生成，不触发发送。
- 主 session key 优先级：
  target.extra.main_session_key > target.session_key > agent.session_key
  > agent.main_session_key > 默认 `xinghui:main`。
- 必须与 rust_main_reply._pick_main_session_key 对齐；漏读 session_key 会把群聊
  时间线误注入到 xinghui:main，污染私聊 clawbot 与 HTTP API 共用会话。
"""
import fcntl
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'plugins' / 'local_frontends'))
try:
    from prompt_config import format_prompt, get_prompt_config
except Exception:
    def format_prompt(_key, default='', **kwargs):
        out = str(default or '')
        for k, v in kwargs.items():
            out = out.replace('{{' + k + '}}', str(v))
        return out
    def get_prompt_config(_path, default=None):
        return default
TEMP_DIR = ROOT / "temp"
TEMP_DIR.mkdir(exist_ok=True)
LOCAL_FRONTENDS_STATE_DIR = ROOT / "plugins" / "local_frontends" / "runtime" / "state"
LOCAL_FRONTENDS_STATE_DIR.mkdir(parents=True, exist_ok=True)


def _read_stdin() -> dict:
    raw = sys.stdin.read() or "{}"
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _pick_main_session_key(agent: dict, target: dict) -> str:
    """Resolve bound main session for inject writes.

    Align with rust_main_reply._pick_main_session_key (minus inject self-ref):
    extra.main_session_key > target.session_key > agent.session_key
    > agent.main_session_key > xinghui:main.
    """
    extra = target.get("extra") or {}
    key = (
        extra.get("main_session_key")
        or target.get("session_key")
        or agent.get("session_key")
        or agent.get("main_session_key")
        or "xinghui:main"
    )
    return str(key)


def _safe_agent_name(agent: dict, main_key: str) -> str:
    raw = str(agent.get("name") or agent.get("agent_name") or main_key.split(":", 1)[0] or "xinghui")
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in ("_", "-"))
    return safe or "xinghui"


def _context_file(agent_name: str) -> Path:
    # Legacy per-agent context path kept only for diagnostics / backward compatibility.
    return TEMP_DIR / f"agent_context_{agent_name}.json"


def _shared_session_file() -> Path:
    # Primary store shared with clawbot / WeChat private / topic summaries.
    # GA root temp is disposable and must not be the reusable plugin state source.
    return LOCAL_FRONTENDS_STATE_DIR / "wechat_session_state.json"


def _format_time(raw) -> str:
    text = str(raw or "").strip()
    return text or datetime.now().isoformat(timespec="seconds")


def _sender_label(msg: dict) -> str:
    sender = str(msg.get("sender") or "").strip()
    if msg.get("is_self"):
        # Keep a stable first-person label for self messages so the model
        # never confuses our own bubble with another group member.
        return "我"
    return sender or "对方"


def _build_timeline_text(target: dict, messages: list, prompt: str) -> str:
    """兼容旧调用：把 Rust 原始消息转成可读时间线文本。新注入路径会逐条生成 items。"""
    items = _build_items(target, messages, prompt, include_instruction=True)
    if items:
        return "\n\n".join(str(item.get("text") or "").strip() for item in items if str(item.get("text") or "").strip())
    return str(prompt or "").strip()


def _build_items(target: dict, messages: list, prompt: str, include_instruction: bool = True) -> list:
    """把 Rust 传入的原始 messages 按真实时间线逐条注入：来源 + 时间 + 发言人 + 内容。"""
    title = str(target.get("wechat_title") or target.get("name") or target.get("id") or "微信会话")
    kind = str(target.get("kind") or "chat")
    source = f"微信{'群聊' if kind == 'group' else '私聊'}「{title}」"
    built = []
    for msg in messages if isinstance(messages, list) else []:
        if not isinstance(msg, dict):
            continue
        text = str(msg.get("text") or "").strip()
        if not text:
            continue
        # Identity must stay on the structured item. Downstream formatters
        # (main reply / history) decide "本账号 vs 群友" from is_self / sender_user,
        # not from display-name heuristics. Dropping these fields used to make
        # self messages look like a peer named "我".
        is_self = bool(msg.get("is_self"))
        raw_sender = str(msg.get("sender") or "").strip()
        sender_user = str(msg.get("sender_user") or msg.get("user") or msg.get("wxid") or "").strip()
        sender = _sender_label(msg)
        ts = _format_time(msg.get("ts"))
        line = f"[{source}] {ts} {sender}: {text}"
        built.append(
            {
                "role": "user",
                "text": line,
                "ts": ts,
                "target_id": target.get("id"),
                "sender": sender,
                "display_name": raw_sender or sender,
                "sender_user": sender_user,
                "is_self": is_self,
                "kind": target.get("kind"),
                "wechat_title": target.get("wechat_title"),
                "message_id": msg.get("id"),
                "msg_id": msg.get("id"),
                "source": source,
            }
        )
    if built and include_instruction:
        rules = target.get("rules") or {}
        aliases = rules.get("user_aliases") or []
        primary_user = ""
        if isinstance(aliases, list):
            for a in aliases:
                s = str(a or "").strip()
                if s:
                    primary_user = s
                    break
        if kind == "group":
            parts = [
                format_prompt('rust_session_inject.group_instruction_base', '请结合以上 {{source}} 按时间顺序刚注入的新消息自然参与聊天。', source=source),
            ]
            if primary_user:
                parts.append(
                    format_prompt('rust_session_inject.group_primary_user_hint', '其中 {{primary_user}} 是当前配置的用户/特殊关系对象；看到这个名字时按底层人设自然对待。', primary_user=primary_user)
                )
                parts.append(
                    format_prompt('rust_session_inject.group_privacy_hint', '这是公开群聊，不要主动在群里谈论 {{primary_user}} 的任何隐私；别人问及时要巧妙回避。', primary_user=primary_user)
                )
            parts.append(
                format_prompt(
                    'rust_session_inject.group_self_identity_hint',
                    '时间线里显示名为“我”、或标注为本账号/is_self 的消息，是你自己已经发出的内容；不要当成别人说的话去接、去反问、去重复报到或替“别人”解释你刚说过的话。'
                    '反过来：显示名像AGENT_DISPLAY_NAME/AGENT_DISPLAY_NAME/AGENT_ALIAS/AGENT_NICKNAME/AGENT_ALIAS_2，但未标为本账号/is_self 的，是其他群友或其他赛博AGENT_DISPLAY_NAME，绝不是你；不要代其发言，也不要用显示名把对方认成自己。同名必须按 is_self 与账号区分。'
                    '若消息带有 [微信@] 行：@本账号(…) 才是官方@到你；@非本账号(…) 是在@别人（即使正文写着@AGENT_DISPLAY_NAME/同名昵称）。无 [微信@] 时不要仅凭正文@昵称认定在喊你。这只帮助辨认@对象，不强制你必须回复。',
                )
            )
            parts.append(format_prompt('rust_session_inject.group_output_rule', '只给出将发送到该微信群的正文，不要解释内部判断。'))
            instruction_text = "".join(parts)
        else:
            instruction_text = format_prompt(
                'rust_session_inject.private_instruction',
                '请结合以上 {{source}} 按时间顺序刚注入的新消息自然参与聊天；如果回应，只给出将发送到微信的正文，不要解释内部判断。',
                source=source,
            )
        built.append(
            {
                "role": "user",
                "text": instruction_text,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "target_id": target.get("id"),
                "sender": "__multi_chat_instruction__",
                "kind": target.get("kind"),
                "wechat_title": target.get("wechat_title"),
                "message_count": len(built),
                "source": source,
            }
        )
    if not built:
        fallback = str(prompt or "").strip()
        if fallback:
            return [
                {
                    "role": "user",
                    "text": fallback,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "target_id": target.get("id"),
                    "sender": "__multi_chat_prompt__",
                    "kind": target.get("kind"),
                    "wechat_title": target.get("wechat_title"),
                }
            ]
    return built


def _trim_list(values, max_len: int):
    if not isinstance(values, list):
        return []
    if len(values) > max_len:
        return values[-max_len:]
    return values


def _item_dedupe_key(item: dict, content: str) -> str:
    """Stable key for injected WeChat timeline entries.

    Prefer DB message_id when available; fall back to a short content signature so
    repeated sliding-window injections do not keep appending the same message.
    """
    target_id = str(item.get("target_id") or "")
    message_id = str(item.get("message_id") or "").strip()
    if message_id:
        return f"wechat_msg:{target_id}:{message_id}"
    sender = str(item.get("sender") or "")
    ts = str(item.get("ts") or "")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]
    return f"wechat_sig:{target_id}:{sender}:{ts}:{digest}"


def _entry_dedupe_key(entry: dict):
    if not isinstance(entry, dict):
        return None
    meta = entry.get("wechat_inject") if isinstance(entry.get("wechat_inject"), dict) else {}
    key = meta.get("dedupe_key")
    if key:
        return str(key)
    content = str(entry.get("content") or "")
    if "[来源: 非clawbot微信/" not in content:
        return None
    return "legacy:" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]


def _append_to_context(agent_name: str, source: str, main_key: str, target: dict, items: list) -> str:
    """用文件锁把非 clawbot 微信上下文追加到 clawbot 共用的 xinghui:main session。"""
    path = _shared_session_file()
    legacy_path = _context_file(agent_name)
    lock_path = path.with_suffix(path.suffix + ".lock")
    now = datetime.now().isoformat(timespec="seconds")
    lock_path.parent.mkdir(exist_ok=True)

    with open(lock_path, "w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if path.exists():
                try:
                    all_data = json.loads(path.read_text("utf-8"))
                except Exception:
                    all_data = {}
            else:
                all_data = {}
            if not isinstance(all_data, dict):
                all_data = {}

            sess = all_data.get(main_key)
            if not isinstance(sess, dict):
                sess = {}

            backend = sess.get("backend_history") if isinstance(sess.get("backend_history"), list) else []
            agent_hist = sess.get("agent_history") if isinstance(sess.get("agent_history"), list) else []
            # One-time/self-healing cleanup: old versions stored generation instructions
            # or full Rust prompt as user history. Scheme A keeps only real WeChat
            # timeline entries in backend_history; current-turn instruction lives in
            # main_session_wrapper.txt and must not accumulate here.
            def _is_old_generation_instruction(e):
                if not isinstance(e, dict):
                    return False
                content_s = str(e.get("content") or "")
                inj = e.get("wechat_inject")
                inj_sender = ""
                if isinstance(inj, dict):
                    inj_sender = str(inj.get("sender") or "")
                return (
                    inj_sender in {"__multi_chat_instruction__", "__multi_chat_prompt__"}
                    or any(str(m) in content_s for m in (get_prompt_config('rust_session_inject.legacy_instruction_markers', []) or []))
                )

            # Drop old raw non-clawbot WeChat timeline entries from persistent session.
            # Raw/high-frequency group observations are current-turn runtime context only;
            # durable cross-port timeline should be injected by rust_topic_summary.py
            # as compact chronological summaries.
            def _is_raw_wechat_observation(e):
                if not isinstance(e, dict):
                    return False
                content = e.get("content")
                parts = []
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            parts.append(str(block.get("text") or ""))
                        elif isinstance(block, str):
                            parts.append(block)
                content_s = "\n".join(p for p in parts if p)
                inj = e.get("wechat_inject")
                if isinstance(inj, dict) and str(inj.get("source") or "").startswith("multi_chat:"):
                    return True
                return (
                    content_s.startswith("[来源: 非clawbot微信/")
                    or content_s.startswith("[微信群聊观察记录｜")
                    or re.match(r"^\[群聊·[^\]\n]+\]\s+\d{1,2}:\d{2}(?::\d{2})?\s+[^\n:：]{1,80}[:：]\s+", content_s.strip()) is not None
                )

            backend = [
                e for e in backend
                if not _is_old_generation_instruction(e) and not _is_raw_wechat_observation(e)
            ]

            # Do not persist fresh raw group/private observations here. The returned
            # `items` are still used by the Rust caller for this turn's runtime prompt,
            # while durable group context is handled by async topic summaries.

            sess["backend_history"] = _trim_list(
                backend,
                int(os.environ.get("GA_WECHAT_MULTI_BACKEND_HISTORY_MAX", "50")),
            )
            sess["agent_history"] = _trim_list(
                agent_hist,
                int(os.environ.get("GA_WECHAT_MULTI_AGENT_HISTORY_MAX", "400")),
            )
            sess["last_update"] = now
            sess["last_source"] = source
            sess["last_context_file"] = str(path)
            sess["legacy_context_file"] = str(legacy_path)
            all_data[main_key] = sess

            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    return str(path)


def main() -> None:
    req = _read_stdin()
    agent = req.get("agent") or {}
    target = req.get("target") or {}
    prompt = req.get("prompt") or ""
    messages = req.get("messages") if isinstance(req.get("messages"), list) else []
    target_id = target.get("id", "unknown")
    source = f"multi_chat:{target_id}:inject"
    main_key = _pick_main_session_key(agent, target)
    agent_name = _safe_agent_name(agent, main_key)

    try:
        items = _build_items(target, messages, "", include_instruction=False)
        context_file = _append_to_context(agent_name, source, main_key, target, items) if items else str(_context_file(agent_name))
        out = {
            "injected": bool(items),
            "main_session_key": main_key,
            "source": source,
            "items": items,
            "context_file": context_file,
            "agent_name": agent_name,
        }
        print(json.dumps(out, ensure_ascii=False))
    except Exception as e:  # pragma: no cover - adapter safety net
        print(
            json.dumps(
                {
                    "injected": False,
                    "main_session_key": main_key,
                    "source": source,
                    "items": [],
                    "adapter_error": repr(e),
                    "target": target_id,
                    "agent_name": agent_name,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()