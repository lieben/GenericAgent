"""Isolated GA runtime adapter used only by the roleplay process."""
from __future__ import annotations

import queue
import re
import threading

import agentmain
from plugins.local_frontends.session_history import (
    persist_agent_backend_history,
    restore_backend_history,
)
from . import storage

_agents: dict[str, agentmain.GenericAgent] = {}
_locks: dict[str, threading.RLock] = {}
_guard = threading.RLock()
# GenericAgent resolves these agentmain globals on every turn. Serialize the
# replacement for the whole generation so two roleplay sessions cannot mix prompts.
_runtime_lock = threading.RLock()


def _agent(key: str) -> agentmain.GenericAgent:
    with _guard:
        if key not in _agents:
            # Construction may reload normal tool schemas, so it also lives inside
            # _runtime_lock and is followed by a hard tools-off assignment.
            agent = agentmain.GenericAgent()
            agent.history = []
            agent.inc_out = True
            restore_backend_history(agent, key, storage.HISTORY)
            worker = threading.Thread(target=agent.run, daemon=True, name=f"roleplay-agent-{key}")
            worker.start()
            _agents[key] = agent
            _locks[key] = threading.RLock()
        return _agents[key]


_INTERNAL_BLOCK_RE = re.compile(
    r"(?is)"
    r"<summary\b[^>]*>.*?(?:</summary>|$)\s*|"
    r"<analysis\b[^>]*>.*?(?:</analysis>|$)\s*|"
    r"<thinking\b[^>]*>.*?(?:</thinking>|$)\s*|"
    r"\*\*\s*LLM\s+Running\b.*?\*\*\s*|"
    r"```\s*\[Info\]\s*Final response to user\.\s*```\s*"
)
_FINAL_MARK_RE = re.compile(
    r"(?is)(?:^|\n)\s*(?:\[Info\]\s*Final response to user\.?|Final response to user\.?|最终回复|正文\s*[:：]|Response)\s*[:：]?\s*(.+)$"
)
_INTERNAL_LINE_RE = re.compile(
    r"(?i)^\s*(?:LLM\s+Running\b|Exploring\b|We need\b|Need\b|Current turn\s*:|\[SYSTEM\]|\[WORKING MEMORY\]|<summary\b|</summary>)"
)
_INTERNAL_HEADING_RE = re.compile(r"^\s*\*\*\s*[A-Z][A-Za-z0-9 ,:'’/()\-]+\s*\*\*\s*$")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")

_HIDDEN_TAGS = (
    r"summary|think|thinking|thought|analysis|reasoning|reasoning_content|"
    r"function_calls?|function_results?|tool_calls?|tool_results?"
)
_TOKEN_RE = re.compile(rf"<\s*(/?)\s*({_HIDDEN_TAGS})\b[^>]*>", re.IGNORECASE)
_TRAILING_TAG_RE = re.compile(rf"<\s*/?\s*(?:{_HIDDEN_TAGS})\b[^>]*\Z", re.IGNORECASE)


def _strip_hidden_tags(text: str) -> str:
    """与 ga_api_server 对齐的标准隐藏标签状态机：过滤 think/thought/reasoning 等协议块（含未闭合块）。"""
    value = str(text or "")
    chunks, stack, cursor = [], [], 0
    for match in _TOKEN_RE.finditer(value):
        if not stack:
            chunks.append(value[cursor:match.start()])
        name = match.group(2).lower()
        if match.group(1):
            if name in stack:
                index = len(stack) - 1 - stack[::-1].index(name)
                del stack[index:]
        elif not re.search(r"/\s*>$", match.group(0)):
            stack.append(name)
        cursor = match.end()
    if not stack:
        chunks.append(value[cursor:])
    value = "".join(chunks)
    return _TRAILING_TAG_RE.sub("", value)


def _truncate_backend_history(hist: list, turns: int) -> list:
    """Trim backend history to at most `turns` dialogue rounds (pairs of user/assistant)."""
    if not isinstance(hist, list) or turns <= 0:
        return []
    # Each round is 1 user + 1 assistant message, so keep at most 2 * turns messages
    keep_n = turns * 2
    trimmed = hist[-keep_n:] if len(hist) > keep_n else list(hist)
    # Ensure history starts with a user message for protocol compliance
    while trimmed and trimmed[0].get("role") != "user":
        trimmed.pop(0)
    return trimmed


def _drop_leading_internal_sections(text: str) -> str:
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index >= len(lines) or not _INTERNAL_HEADING_RE.match(lines[index]):
            break
        index += 1
        while index < len(lines):
            line = lines[index]
            if _INTERNAL_HEADING_RE.match(line):
                break
            if _CJK_RE.search(line):
                return "\n".join(lines[index:]).strip()
            index += 1
    return "\n".join(lines[index:]).strip()


def _drop_leading_english_preamble(text: str) -> str:
    """剔除模型开头的思维构思草稿（例如开头复述需求、英文大纲拆解等）。"""
    s = text.strip()

    # 场景1：开头包含一小句中文标记（如 "番外) "、"构思："）后跟着大段英文分析直到故事标题
    cot_leak = re.search(
        r"(?is)\b(?:Let me analyze|I need to write|I should write|The user wants|Writing plan|Scenario outline)\b[\s\S]*?(?=(?:#+\s*|【|第[一二三四五六七八九十\d]+章))",
        s,
    )
    if cot_leak and cot_leak.start() < 200:
        return s[cot_leak.end():].strip()

    match = _CJK_RE.search(s)
    if not match:
        return s

    # 场景2：大段英文思考/构思直接开头，直到正文首段中文或 Markdown 标题
    cjk_idx = match.start()
    last_nl = s.rfind("\n", 0, cjk_idx)
    line_start = 0 if last_nl == -1 else last_nl + 1
    prefix_lines = s[:line_start].strip()
    current_line_prefix = s[line_start:cjk_idx]

    if prefix_lines and re.search(r"[A-Za-z]", prefix_lines) and not _CJK_RE.search(prefix_lines):
        s = s[line_start:].strip()
    elif not prefix_lines and current_line_prefix and re.search(r"[A-Za-z]", current_line_prefix):
        s = s[cjk_idx:].lstrip(" `：:，,。.!！?？\n\t")

    return s.strip()


def _visible_text(raw: str) -> str:
    text = str(raw or "")
    match = _FINAL_MARK_RE.search(text)
    if match:
        text = match.group(1)
    # 1. 隐藏标签过滤（think/thought/reasoning 等）
    text = _strip_hidden_tags(text)
    # 2. 内部块与行过滤
    text = _INTERNAL_BLOCK_RE.sub("", text)
    text = _drop_leading_internal_sections(text)
    text = _drop_leading_english_preamble(text)
    lines = [line for line in text.splitlines() if not _INTERNAL_LINE_RE.search(line)]
    return "\n".join(lines).strip()


def _suffix_delta(previous: str, current: str) -> str:
    if current.startswith(previous):
        return current[len(previous):]
    return current


def generate(slug: str, session_id: str, text: str, extra_system: str = ""):
    text = str(text or "").strip()
    if not text:
        raise ValueError("message is required")
    storage.get_session(slug, session_id)
    key = storage.session_key(slug, session_id)
    prompt = storage.prompt_for(slug, session_id, current_query=text)
    extra_system = str(extra_system or "").strip()
    if extra_system:
        prompt = f"{prompt}\n\n【第三方平台本轮补充设定】\n{extra_system}"

    # _runtime_lock is process-wide because the upstream runtime reads global
    # get_system_prompt and TOOLS_SCHEMA dynamically. The standalone roleplay
    # process never serves normal GA sessions, so this cannot affect the main runtime.
    with _runtime_lock:
        agentmain.TOOLS_SCHEMA = []
        agentmain.get_system_prompt = lambda: prompt
        agent = _agent(key)
        # GenericAgent construction may reload normal tools; enforce tools-off
        # after construction as well, before the task can begin.
        agentmain.TOOLS_SCHEMA = []

        # Enforce canon-configured dialogue turns onto backend history before run
        session_data = storage.get_session(slug, session_id)
        canon_cfg = session_data.get("canon", {}).get("context_config", {})
        dialogue_turns = int(canon_cfg.get("dialogue_turns", 8))
        if hasattr(agent, "llmclient") and hasattr(agent.llmclient, "backend"):
            raw_backend_hist = getattr(agent.llmclient.backend, "history", [])
            trimmed_hist = _truncate_backend_history(raw_backend_hist, dialogue_turns)
            agent.llmclient.backend.history = trimmed_hist

        with _locks[key]:
            q = agent.put_task(text, source=key)
            raw_result = ""
            visible_result = ""
            sent_visible = ""
            while True:
                try:
                    item = q.get(timeout=1800)
                except queue.Empty as exc:
                    raise TimeoutError("roleplay generation timeout") from exc
                if "next" in item:
                    raw_result += str(item.get("next", ""))
                    cur_visible = _visible_text(raw_result)
                    if cur_visible and cur_visible.startswith(sent_visible) and len(cur_visible) > len(sent_visible):
                        delta = cur_visible[len(sent_visible):]
                        sent_visible = cur_visible
                        yield delta
                    continue
                if "done" in item:
                    raw_result = str(item.get("done", "")) or raw_result
                    visible_result = _visible_text(raw_result)
                    if visible_result and len(visible_result) > len(sent_visible):
                        if visible_result.startswith(sent_visible):
                            delta = visible_result[len(sent_visible):]
                        else:
                            delta = visible_result
                        yield delta
                    elif not sent_visible and visible_result:
                        yield visible_result
                    break
                if "error" in item:
                    raise RuntimeError(f"roleplay backend error: {item['error']}")

            # Post-run: trim backend history again to preserve only canon-configured turns
            if hasattr(agent, "llmclient") and hasattr(agent.llmclient, "backend"):
                raw_backend_hist = getattr(agent.llmclient.backend, "history", [])
                trimmed_hist = _truncate_backend_history(raw_backend_hist, dialogue_turns)
                agent.llmclient.backend.history = trimmed_hist
                # Force persist trimmed history directly into storage.HISTORY to bypass shrink-refusal
                from plugins.local_frontends.session_history import load_session_state, save_session_state, _SESSION_LOCK, _sanitize_backend_history
                with _SESSION_LOCK:
                    data = load_session_state(storage.HISTORY)
                    entry = data.get(key)
                    if not isinstance(entry, dict):
                        entry = {}
                    entry['backend_history'] = _sanitize_backend_history(trimmed_hist)
                    entry.setdefault('agent_history', [])
                    data[key] = entry
                    save_session_state(data, storage.HISTORY)
            storage.append_turn(slug, session_id, text, visible_result)
            return visible_result


def chat(slug: str, session_id: str, text: str, extra_system: str = "") -> str:
    result = ""
    for piece in generate(slug, session_id, text, extra_system=extra_system):
        result += piece
    return result


def reset_runtime(slug: str, session_id: str) -> None:
    """Drop only the in-process cache; persisted isolated history remains."""
    key = storage.session_key(slug, session_id)
    with _runtime_lock, _guard:
        _agents.pop(key, None)
        _locks.pop(key, None)
