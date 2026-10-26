#!/usr/bin/env python3
"""Clawbot native-GA compatibility entrypoint.

Runs the official ``frontends.wechatapp`` implementation, but installs a tiny
hook layer for clawbot-only plugins.  No custom wechatapp, no multi-agent layer,
and no edits to official GA core files.
"""
import os
import sys
import socket
import threading
import re
import time
import importlib
import subprocess
import json
import ast
import html
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict


def _find_project_root() -> Path:
    p = Path(__file__).resolve()
    for d in [p.parent, *p.parents]:
        if (d / "agentmain.py").exists() and (d / "frontends" / "wechatapp.py").exists():
            return d
    return p.parents[2]

ROOT = _find_project_root()
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
L3_SHARED = ROOT / "memory" / "L3_shared"
if L3_SHARED.exists():
    sys.path.insert(0, str(L3_SHARED))

from plugins.local_frontends.shared_chat_runtime import (
    enqueue_frontend_task,
    persist_frontend_session,
)
from plugins.local_frontends.prompt_config import format_prompt, get_prompt_config, get_agent_display_name, get_user_display_name

SHARED_HOOK_APP = None

# clawbot default plugin knobs; existing environment may override them.
os.environ.setdefault('WECHAT_PLUS_RUNTIME_CONTEXT', '1')
os.environ.setdefault('WECHAT_PLUS_BUBBLE_SPLIT', '1')
os.environ.setdefault('WECHAT_PLUS_PROACTIVE', '1')

os.environ.setdefault('WECHAT_PLUS_GROUP_PEEK_MINUTES', '10')


def _l4_archive_day(now: datetime | None = None) -> str:
    """Return native emotional-line L4 archive day; day rolls at 08:00."""
    now = now or datetime.now()
    if now.hour < 8:
        now = now - timedelta(days=1)
    return now.strftime('%Y-%m-%d')


def _wechat_send_ok(ret) -> bool:
    """Return True only when iLink accepted a visible outgoing message.

    Historical success shapes observed in production:
    - ``{}``
    - ``{'ret': 0}`` / ``{'ret': '0'}``
    - ``{'message_id': <int/str>}``  (delivery accepted; no error code)
    If ``ret`` is present and non-zero, treat as failure even when message_id exists.
    """
    if ret == {}:
        return True
    if not isinstance(ret, dict):
        return False
    if 'ret' in ret:
        try:
            return int(ret.get('ret')) == 0
        except Exception:
            return False
    mid = ret.get('message_id')
    return mid is not None and str(mid).strip() != ''


def _append_l4_wechat(role: str, text: str, uid: str = '') -> None:
    """Append visible clawbot dialogue to native GA L4 mainline archive.

    This keeps L4 stable for agent_dream_review without modifying GA core or
    official frontends.wechatapp. Only user-visible incoming/outgoing dialogue is
    archived; tool logs and internal blocks are intentionally excluded upstream by
    _visible_dialog_only before assistant sends are recorded.
    """
    text = str(text or '').strip()
    if not text:
        return
    try:
        now = datetime.now()
        day = _l4_archive_day(now)
        out_dir = ROOT / 'memory' / 'agents' / _EMOJI_AGENT_NAME / 'L4_sessions' / 'wechat' / 'mainline'
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f'{day}.md'
        if not out.exists():
            out.write_text(format_prompt('wechat_media_batch.l4_archive_title', '# clawbot archive · {{day}}\n\n', day=day), encoding='utf-8')
        role_label = get_user_display_name('user') if role == 'user' else get_agent_display_name('assistant')
        uid_part = f' uid={uid}' if uid else ''
        with out.open('a', encoding='utf-8') as f:
            f.write(f'\n## {now:%H:%M:%S} {role_label}{uid_part}\n\n{text}\n')
    except Exception as e:
        print(f'[clawbot-l4] append failed: {type(e).__name__}: {e}', file=sys.__stdout__)


class HookApp:
    def __init__(self, wx):
        self.wx = wx
        self.agent = wx.agent
        self._real_agent = wx.agent
        self.hooks = defaultdict(list)
        self._hook_keys = set()
        self.last_uid = ''
        self.last_context_token = ''
        self.last_incoming_text = ''
        self.pending_visible_user_prompt = ''
        self.bot = None
        # Debounce text bursts per clawbot uid before handing them to the
        # official frontend worker.  This avoids adjacent-message races on the
        # single shared xinghui:main backend history while preserving the last
        # user message as the one to answer.
        self.debounce_lock = threading.Lock()
        self.debounce_by_uid = {}
        # Serialize full clawbot turns on the single shared xinghui:main backend
        # history.  A new message may arrive after the previous answer was sent
        # to WeChat but before native backend.history/session persistence is
        # complete; without this lock, the next restore can see a one-turn-old
        # history and make replies look like they answer adjacent pairs (ab/bc).
        self.serial_turn_lock = threading.Lock()
    def register_hook(self, name, fn):
        key = (name, getattr(fn, '__module__', '?'), getattr(fn, '__name__', repr(fn)))
        if key in self._hook_keys:
            print(f'[clawbot-hook] duplicate skipped {name}: {key[1]}.{key[2]}', file=sys.__stdout__)
            return
        self._hook_keys.add(key)
        self.hooks[name].append(fn)
        print(f'[clawbot-hook] registered {name}: {key[1]}.{key[2]}', file=sys.__stdout__)

    def emit(self, name, *args, **kwargs):
        value = args[0] if args else None
        for fn in list(self.hooks.get(name, [])):
            try:
                r = fn(*args, **kwargs)
                if r is not None and name in ('before_put_task', 'before_send_text'):
                    value = r
                    if args:
                        if name == 'before_put_task' and len(args) >= 2:
                            # before_put_task hooks are called as (uid, prompt).
                            # Hook return values replace only the prompt; keeping
                            # uid stable lets later hooks distinguish API/clawbot.
                            args = (args[0], r, *args[2:])
                        else:
                            args = (r, *args[1:])
            except Exception as e:
                print(f'[clawbot-hook] {name} error in {getattr(fn, "__module__", "?")}: {type(e).__name__}: {e}', file=sys.__stdout__)
        return value

    # Used by proactive plugin.
    def send_text(self, text, to_user_id=None, context_token=None):
        if not self.bot:
            raise RuntimeError('bot is not ready')
        uid = to_user_id or self.last_uid
        if not uid:
            raise RuntimeError('no target uid for proactive send')
        ctx = self.last_context_token if context_token is None else context_token
        return self.bot.send_text(uid, text, context_token=ctx)


# Persistent native-GA backend history bridge for clawbot main session.
# Keep this in the frontend layer only: group peek/context remains side-channel
# prompt material and must not be written into backend_history.
_SESSION_STATE_PATH = ROOT / 'plugins' / 'local_frontends' / 'runtime' / 'state' / 'wechat_session_state.json'
_SESSION_KEY = os.environ.get('WECHAT_PLUS_SESSION_KEY', 'xinghui:main')
_SESSION_LOCK = threading.RLock()


def _load_session_state() -> dict:
    try:
        if _SESSION_STATE_PATH.exists():
            data = json.loads(_SESSION_STATE_PATH.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f'[clawbot-session] load state failed: {type(e).__name__}: {e}', file=sys.__stdout__)
    return {}


def _save_session_state(data: dict) -> None:
    try:
        _SESSION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SESSION_STATE_PATH.with_suffix(_SESSION_STATE_PATH.suffix + '.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        tmp.replace(_SESSION_STATE_PATH)
    except Exception as e:
        print(f'[clawbot-session] save state failed: {type(e).__name__}: {e}', file=sys.__stdout__)


def _get_backend_history(agent):
    try:
        hist = agent.llmclient.backend.history
        return hist if isinstance(hist, list) else []
    except Exception:
        return []


def _set_backend_history(agent, hist) -> bool:
    if not isinstance(hist, list):
        return False
    try:
        agent.llmclient.backend.history = hist
        return True
    except Exception as e:
        print(f'[clawbot-session] set backend history failed: {type(e).__name__}: {e}', file=sys.__stdout__)
        return False


def _content_to_text_blocks(content):
    """Return GA native text-block content for backend.history."""
    text = _extract_text_payload(content)
    if not isinstance(text, str):
        text = str(text or '')
    text = text.strip()
    return [{'type': 'text', 'text': text}] if text else []


def _sanitize_backend_history(hist):
    """Keep persistent clawbot backend history to visible dialogue only.

    This is a local_frontends guard only: GA core history is not changed until
    clawbot explicitly restores/saves its fixed session.  Tool/thinking/summary
    debris may appear in backend.history depending on model adapter output; it
    must not become long-lived prompt context for xinghui:main.

    Native GA sessions expect message content to be a list of text blocks.  If
    clawbot restores plain strings, NativeClaudeSession.ask() will list(str)
    and split history into single characters, making context effectively lost.
    """
    if not isinstance(hist, list):
        return []
    cleaned = []
    skip_next_title_reply = False
    for item in hist:
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        if role not in ('system', 'user', 'assistant'):
            continue
        new_item = dict(item)
        content = new_item.get('content')
        if role == 'assistant':
            content = _extract_text_payload(content)
            content = _visible_dialog_only(content)
            text = str(content or '').strip()
            if skip_next_title_reply and _looks_like_title_reply(text):
                skip_next_title_reply = False
                continue
            skip_next_title_reply = False
            if not text:
                continue
            new_item['content'] = _content_to_text_blocks(text)
        elif isinstance(content, str):
            # User/system entries should remain semantically intact, but stale
            # tool envelopes or old <summary> blocks must not persist either.
            # Old runs may persist OpenAI-style content arrays as a string
            # ("[{'type':'text','text':'...'}]"); parse them before cleanup so
            # injected SYSTEM preambles and summary-format rules do not survive.
            content = _maybe_parse_text_payload(content)
            if _is_internal_title_task(str(content or '')):
                skip_next_title_reply = True
                continue
            content = _visible_dialog_only(content)
            if not str(content or '').strip():
                continue
            skip_next_title_reply = False
            new_item['content'] = _content_to_text_blocks(content)
        elif isinstance(content, list):
            # Some adapters keep user messages as OpenAI-style content arrays.
            # Persist only the visible text, never injected SYSTEM wrappers.
            content = _extract_text_payload(content)
            if _is_internal_title_task(str(content or '')):
                skip_next_title_reply = True
                continue
            content = _visible_dialog_only(content)
            if not str(content or '').strip():
                continue
            skip_next_title_reply = False
            new_item['content'] = _content_to_text_blocks(content)
        cleaned.append(new_item)
    return cleaned


def _restore_backend_history_from_session(agent, force: bool = False) -> int:
    with _SESSION_LOCK:
        current = _get_backend_history(agent)
        if current and not force:
            return len(current)
        data = _load_session_state()
        entry = data.get(_SESSION_KEY) if isinstance(data, dict) else None
        hist = entry.get('backend_history') if isinstance(entry, dict) else None
        if isinstance(hist, list) and hist:
            clean_hist = _sanitize_backend_history(hist)
            _set_backend_history(agent, clean_hist)
            if clean_hist != hist:
                entry['backend_history'] = clean_hist
                data[_SESSION_KEY] = entry
                _save_session_state(data)
                print(f'[clawbot-session] sanitized restored backend_history key={_SESSION_KEY} n={len(clean_hist)}', file=sys.__stdout__)
            print(f'[clawbot-session] restored backend_history key={_SESSION_KEY} n={len(clean_hist)}', file=sys.__stdout__)
            return len(clean_hist)
        return len(current)


_FRONTEND_CONTEXT_MARKER_RE = re.compile(r"【\.([^【】\r\n]{1,80}?)\.】")


def _extract_frontend_context_marker(text: str) -> tuple[str | None, str]:
    """Extract a configurable frontend marker like 【.微信平台跨次元对话.】.

    The dot-wrapped marker is control metadata authored in frontend config.  It is
    kept in shared session history as the original compact marker
    (for example: 【.微信平台跨次元对话.】) so clawbot and API can share the
    same scene/output hint shape without persisting the whole runtime SYSTEM wrapper.
    """
    if not text:
        return None, text or ''
    match = _FRONTEND_CONTEXT_MARKER_RE.search(str(text))
    if not match:
        return None, str(text)
    label = match.group(1).strip()
    marker = match.group(0).strip()
    cleaned = (str(text)[:match.start()] + str(text)[match.end():]).strip()
    return (marker if label else None), cleaned


def _build_clawbot_history_user_prompt(visible_text: str) -> str:
    """Build the compact user history item for clawbot's shared xinghui session.

    Current-turn generation may receive a configurable entry prompt such as
    ``【SYSTEM 当前前端入口说明：{{frontend_instruction}}】``.  Long-lived history
    should mirror the API behavior: keep only a compact marker extracted from
    ``runtime.frontend_instruction`` plus the real visible user text.
    """
    text = str(visible_text or '').strip()
    if not text:
        return ''

    frontend_instruction = str(get_prompt_config('runtime.frontend_instruction', '') or '')
    label, _ = _extract_frontend_context_marker(frontend_instruction)
    if not label:
        # Allow operators to put the marker in the actual visible text, matching
        # API semantics, but do not require or hardcode any specific scene name.
        label, text = _extract_frontend_context_marker(text)
    if not label:
        return text

    user_name = get_user_display_name('user')
    return f'{label}\n{user_name}：{text}'


def _replace_last_user_history(agent, visible_text: str) -> bool:
    """Replace the current turn's user message with the frontend-visible text.

    Clawbot may prepend per-turn runtime context before enqueueing native GA so
    the model can see time/group side context for this turn.  That wrapper must
    not become the long-lived xinghui:main session history; session history keeps
    only the visible user message that came from the frontend.
    """
    text = str(visible_text or '').strip()
    if not text:
        return False
    hist = _get_backend_history(agent)
    if not hist:
        return False
    for idx in range(len(hist) - 1, -1, -1):
        item = hist[idx]
        if not isinstance(item, dict) or item.get('role') != 'user':
            continue
        new_item = dict(item)
        new_item['content'] = _content_to_text_blocks(text)
        hist[idx] = new_item
        ok = _set_backend_history(agent, hist)
        if ok:
            print(f'[clawbot-session] replaced last user history with visible prompt len={len(text)}', file=sys.__stdout__)
        return ok
    return False


def _persist_backend_history_to_session(agent) -> int:
    with _SESSION_LOCK:
        hist = _sanitize_backend_history(_get_backend_history(agent))
        # Keep the live backend history sanitized too.  Otherwise a polluted
        # in-memory turn can be re-persisted on the next message even after the
        # state file was cleaned.
        _set_backend_history(agent, hist)
        data = _load_session_state()
        entry = data.get(_SESSION_KEY)
        if not isinstance(entry, dict):
            entry = {}
        # Same guard as session_history.save_backend_history: never let empty /
        # catastrophically short in-memory hist wipe a long disk conversation.
        existing = entry.get('backend_history') if isinstance(entry.get('backend_history'), list) else []
        if existing:
            if not hist:
                print(
                    f'[clawbot-session] refuse empty overwrite key={_SESSION_KEY} keep_n={len(existing)}',
                    file=sys.__stdout__,
                )
                return len(existing)
            if len(existing) >= 10 and len(hist) < max(5, len(existing) // 2):
                print(
                    f'[clawbot-session] refuse shrink overwrite key={_SESSION_KEY} '
                    f'keep_n={len(existing)} got_n={len(hist)}',
                    file=sys.__stdout__,
                )
                return len(existing)
        entry['backend_history'] = hist
        # agent_history is retained for compatibility with older local_frontends
        # readers, but native clawbot does not use it as the LLM backend context.
        entry.setdefault('agent_history', [])
        data[_SESSION_KEY] = entry
        _save_session_state(data)
        print(f'[clawbot-session] persisted backend_history key={_SESSION_KEY} n={len(hist)}', file=sys.__stdout__)
        return len(hist)


def _install_plugins(app):
    specs = []
    if os.environ.get('WECHAT_LIFE_MEMORY', '1') != '0':
        specs.append('plugins.life_memory_plugin')
    if os.environ.get('WECHAT_PLUS_RUNTIME_CONTEXT', '1') != '0':
        specs.append('plugins.local_frontends.wx_plus_runtime_context')
    if os.environ.get('WECHAT_PLUS_BUBBLE_SPLIT', '1') != '0':
        specs.append('plugins.wechat_bubble_split')
    if os.environ.get('WECHAT_PLUS_PROACTIVE', '1') != '0':
        specs.append('plugins.local_frontends.wx_plus_proactive')
    for modname in specs:
        try:
            mod = importlib.import_module(modname)
            installer = getattr(mod, 'install', None) or getattr(mod, 'register', None)
            if installer:
                installer(app)
                msg = f'[clawbot-hook] installed {modname}'
                print(msg, file=sys.__stdout__)
            else:
                msg = f'[clawbot-hook] no installer in {modname}'
                print(msg, file=sys.__stdout__)
        except Exception as e:
            msg = f'[clawbot-hook] install failed {modname}: {type(e).__name__}: {e}'
            print(msg, file=sys.__stdout__)


def _extract_text_payload(obj):
    """Extract visible assistant text from OpenAI/Claude-style response objects.

    Sometimes GA/model logs are stringified as a list like:
    [{'type':'text','text':'real reply'}, {'type':'tool_use', ...}]
    The WeChat last-mile guard must keep only text payloads and ignore tools.
    """
    parts = []
    if obj is None:
        return ''
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        typ = str(obj.get('type', '')).lower()
        if typ in ('tool_use', 'tool_result', 'function_call', 'function', 'tool'):
            return ''
        if 'text' in obj and isinstance(obj.get('text'), str):
            return obj.get('text') or ''
        if 'content' in obj:
            return _extract_text_payload(obj.get('content'))
        if 'message' in obj:
            return _extract_text_payload(obj.get('message'))
        return ''
    if isinstance(obj, (list, tuple)):
        for x in obj:
            t = _extract_text_payload(x)
            if t:
                parts.append(t)
        return '\n'.join(parts)
    return ''


def _maybe_parse_text_payload(s):
    raw = str(s or '').strip()
    if not raw:
        return raw
    # JSON first, then Python repr used in model_responses logs.
    if raw[0] in '[{':
        for parser in (json.loads, ast.literal_eval):
            try:
                extracted = _extract_text_payload(parser(raw))
                if extracted:
                    return extracted
            except Exception:
                pass
    return s


_LEAK_BLOCK_RE = re.compile(
    r'(?is)'
    # Paired internal tags: remove the tag and everything inside.
    r'<summary\b[^>]*>.*?</summary>\s*|'
    r'<analysis\b[^>]*>.*?</analysis>\s*|'
    r'<tool_call\b[^>]*>.*?</tool_call>\s*|'
    r'<tool_response\b[^>]*>.*?</tool_response>\s*|'
    # Agent-shell working-memory fragments may occasionally arrive without the
    # leading "### [WORKING MEMORY]" marker.  Remove the whole XML-like block;
    # deleting only the tag lines would leave old user/assistant summaries as
    # visible WeChat bubbles.
    r'<earlier_context\b[^>]*>.*?</earlier_context>\s*|'
    r'<history\b[^>]*>.*?</history>\s*|'
    r'<key_info\b[^>]*>.*?</key_info>\s*|'
    # GA/clawbot injected prompt block accidentally echoed as assistant text.
    # Marker-based only: remove from [USER]/SYSTEM preamble through the explicit
    # response boundary, not by guessing directive semantics.
    r'^\s*\[USER\]\s*[:：].*?(?:\(Begin your response\)|^\s*Assistant\s*[:：]|^\s*最终回复\s*[:：]|^\s*正文\s*[:：])\s*|'
    r'^\s*[【\[]\s*SYSTEM\b.*?(?:\(Begin your response\)|^\s*Assistant\s*[:：]|^\s*最终回复\s*[:：]|^\s*正文\s*[:：])\s*|'
    r'^\s*[【\[]\s*SYSTEM\b[^\n\]】]*(?:\][^\n]*)?[\]】]\s*\n?|'
    # Whole internal thought/tool blocks are not dialogue.  If no explicit final
    # marker exists in the same payload, suppress them structurally instead of
    # deleting only the marker line and leaking the body after a blank line.
    r'\[THOUGHT\].*?(?=^\s*(?:最终回复|Final response|assistant final|正文)\s*[:：]|\Z)|'
    r'^\s*### \[WORKING MEMORY\].*?(?:</key_info>\s*|\Z)|'
    r'^\s*### \[(?:PLANNING|ACTIONS)\][^\n]*(?:\n|$)|'
    # Runtime/system context blocks injected by the agent shell are never dialogue.
    r'^\s*\[DANGER\][^\n]*(?:\n|$)|'
    r'^\s*\[SYSTEM TIPS\][^\n]*(?:\n|$)|'
    r'^\s*\[SYSTEM\][^\n]*(?:\n|$)|'
    r'^\s*cwd\s*=\s*/[^\n]*(?:\n|$)|'
    r'^\s*\[Memory\][^\n]*(?:\n|$)|'
    r'^\s*\[CONSTITUTION\].*?(?=^\s*\[RULES\]|^\s*\.\./memory/|\Z)|'
    r'^\s*\[RULES\].*?(?=^\s*\.\./memory/|\Z)|'
    r'^\s*\[WORKING MEMORY\].*?(?:</key_info>\s*|\Z)|'
    r'```(?:json|python|bash|javascript)?\s*\{?\s*"?(?:recipient_name|tool_uses|script|cwd|stdout|stderr|exit_code)"?.*?```\s*',
    re.M,
)
_LEAK_LINE_RE = re.compile(
    r'(?i)^\s*(?:'
    # Tag remnants / explicit internal section labels only. Do not guess by Chinese dialogue semantics.
    r'<\s*/?\s*(?:summary|analysis|tool_call|tool_response)\b[^>]*>|'
    r'(?:\*\*\s*)?LLM\s+Running\b|'
    r'(?:analysis|thinking|tool_use|tool_result)\s*[:=：]|'
    r'\[(?:THOUGHT|WORKING MEMORY|SYSTEM|SYSTEM TIPS|DANGER|TOOL|TOOL_CALL|TOOL_RESULT)\]|'
    r'### \[(?:WORKING MEMORY|PLANNING|ACTIONS|TOOL|TOOL_CALL|TOOL_RESULT)\]|'
    r'(?:</?(?:earlier_context|history|key_info)>|If you need to show files to user|必须在回复文本中包含|正在读取记忆或SOP文件|working key_info updated|Please provide your next thought and action/response|\*\*CRITICAL:\*\*|CRITICAL:|MUST include|MUST output text|Do NOT wrap it in a code block|cwd\s*=\s*/|\[Memory\]|\[CONSTITUTION\]|\[RULES\])|'
    r'\b(?:Current\s+turn\s*:|Turn\s+\d+\b)|'
    r'\[FILE:[^\]]+\]|'
    r'\[Image:\s*source:\s*(?:/home|/tmp|/var|/mnt)/[^\]]+\]|'
    r'\d{4}-\d{2}-\d{2}\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b|'
    r'(?:^|\s)/(?:home|tmp|var|mnt)/[^\s\]]*(?:data/agents/xinghui/emojis|emojis)/[^\s\]]*\.(?:png|jpe?g|gif|webp)|'
    r'Last tool call failed|'
    r'(?:^|\s)[\s🛠🔧⚙️\ufe0f]*(?:update_working_checkpoint|start_long_term_update|ask_user|code_run|file_read|file_patch|file_write|web_scan|web_execute_js)\s*\(|'
    r'(?:stdout|stderr|exit_code|recipient_name|tool_uses|inline_eval|save_to_file|switch_tab_id)\b|'
    r'\{\s*"(?:cwd|script|timeout|type|status|stdout|stderr|exit_code|recipient_name|tool_uses)"\s*:'
    r')'
)
_LEAK_TAIL_RE = re.compile(
    r'(?is)'
    r'(?:'
    # Structural task-completion markers only; the optional trailing note before
    # them is handled by _strip_task_done_trailing_note, keyed by marker position.
    r'[\[【(（]?\s*(?:任务\s*已\s*完成|task\s*completed)\s*[\]】)）]?[。.!！]*|'
    # Untagged internal narration sometimes survives when an upstream layer has
    # already stripped <summary> tags.  Limit this to a final third-person
    # process note about the incoming user/media state; never remove ordinary
    # assistant dialogue in the middle of a message.
    + str(get_prompt_config('wechat_media_batch.trailing_process_note_sender_pattern', r'(?:用户|对方)'))
    + r'发送[^\n。.!！?？]{0,80}(?:表情|图片|状态|消息|内容)[^\n。.!！?？]{0,120}(?:安抚|询问|回应|回复|表达|说明|处理)[^\n。.!！?？]{0,80}[。.!！?？]*'
    + r')\s*$'
)
_JSON_TOOL_KEY_RE = re.compile(r'"(?:recipient_name|tool_uses|tool_call|tool_result|stdout|stderr|exit_code|script|cwd|timeout|inline_eval|save_to_file|switch_tab_id)"\s*:')
_DIALOG_MARK_RE = re.compile(r'(?is)(?:最终回复|Final response|assistant final|正文|Response)\s*[:：]\s*(.+)$')
_INTERNAL_TITLE_TASK_RE = re.compile(
    r'(?is)\bBased on the chat history, give this conversation a name\b.*?\bThe name is:\s*$'
)
_INTERNAL_VISION_HINT_RE = re.compile(
    r'(?is)^\s*For image/file vision, use code_run with:.*?(?:\n\s*\n|$)'
)
_CLIENT_FILE_HINT_PREFIX_RE = re.compile(
    r'(?is)^\s*(?:(?:user|用户)\s*[：:]\s*)?If\s+you\s+need\s+to\s+show\s+files\s+to\s+user,\s*use\s+\[FILE:filepath\]\s+in\s+your\s+response\.\s*'
)
# Keep in sync with session_history: clawbot last-mile send must extract the
# same FINAL_ANSWER body that history/persistence already understands.
_FINAL_ANSWER_OPEN = '【FINAL_ANSWER】'
_FINAL_ANSWER_CLOSE = '【/FINAL_ANSWER】'
_FINAL_ANSWER_RE = re.compile(
    re.escape(_FINAL_ANSWER_OPEN) + r'(.*?)' + re.escape(_FINAL_ANSWER_CLOSE),
    re.DOTALL,
)

def _strip_client_file_hint_prefix(s: str) -> str:
    """Drop API/client file-display hint prefix while keeping the real user text."""
    prev = None
    text = str(s or '')
    while prev != text:
        prev = text
        text = _CLIENT_FILE_HINT_PREFIX_RE.sub('', text).lstrip()
    return text

def _extract_final_answer_tagged_tail(s: str) -> str | None:
    """Return the last closed FINAL_ANSWER body, or None if tags are absent."""
    matches = list(_FINAL_ANSWER_RE.finditer(str(s or '')))
    if not matches:
        return None
    return matches[-1].group(1).strip()

def _is_internal_title_task(text: str) -> bool:
    return bool(_INTERNAL_TITLE_TASK_RE.search(str(text or '')))


def _looks_like_title_reply(text: str) -> bool:
    # Title-generation replies are typically one very short line (e.g. "深夜失眠").
    # Only use this immediately after dropping an internal title task.
    s = str(text or '').strip()
    return bool(s) and '\n' not in s and len(s) <= 20


_INTERNAL_FILE_REF_RE = re.compile(
    r'(?is)^\s*(?:'
    r'\[FILE:[^\]]+\]|'
    r'\[图片\s*:\s*(?:/home|/tmp|/var|/mnt)/[^\]]+\]|'
    r'/?(?:home|tmp|var|mnt)/[^\s\]]*(?:data/agents/xinghui/emojis|emojis)/[^\s\]]*\.(?:png|jpe?g|gif|webp)|'
    r'[^\n\r]*微信图片_[^\n\r]*\.(?:png|jpe?g|gif|webp)'
    r')\s*[。.!！?？]*\s*$'
)
_LEGACY_IMAGE_REF_RE = re.compile(
    r'(?is)^\s*\[图片\s*:\s*((?:/home|/tmp|/var|/mnt)/[^\]\r\n]+?)\s*\]\s*[。.!！?？]*\s*$'
)
_EMOJI_AGENT_NAME = os.environ.get('XINGHUI_AGENT_NAME', 'xinghui')
_EMOJI_ROOT = (ROOT / 'data' / 'agents' / _EMOJI_AGENT_NAME / 'emojis').resolve()
_EMOJI_TOKEN_RE = re.compile(r'^\s*\[\[EMOJI:([^\]：|]+)\]\]\s*$')
_TASK_DONE_RE = re.compile(r'(?is)\s*[\[【(（]?\s*(?:任务\s*已\s*完成|task\s*completed)\s*[\]】)）]?[。.!！]*\s*$')

def _strip_task_done_trailing_note(s: str) -> str:
    """Drop one internal note immediately before a task-done marker.

    Keep visible multi-paragraph dialogue intact.  Only when the payload has at
    least three non-empty logical paragraphs before the marker do we treat the
    first paragraph as an internal note and preserve the remaining paragraphs as
    the final visible reply.
    """
    text = str(s or '')
    m = _TASK_DONE_RE.search(text)
    if not m:
        return text
    head = text[:m.start()].rstrip()
    parts = [p.strip() for p in re.split(r'\n\s*\n+', head) if p.strip()]
    if len(parts) >= 3:
        return '\n\n'.join(parts[1:]).rstrip()
    return head

def _prefer_dialog_marked_tail(s: str) -> str:
    """Keep visible dialogue after the last dialogue marker, if present.

    FINAL_ANSWER tags are the primary clawbot transport boundary: when present,
    only the last closed tag body is user-visible.  This matches
    session_history so send-path and history-path cannot drift again.
    """
    text = str(s or '')
    final_answer = _extract_final_answer_tagged_tail(text)
    if final_answer is not None:
        return final_answer
    matches = list(_DIALOG_MARK_RE.finditer(text))
    if not matches:
        return text
    return matches[-1].group(1).lstrip('：: \t')

def _select_emoji_path(category=''):
    category = (category or 'happy').strip() or 'happy'
    script = ROOT / 'plugins' / 'wechat_multi_chat_rust' / 'adapters' / 'emoji_library.py'
    if not script.exists():
        print(f'[clawbot-hook] emoji library missing: {script}', file=sys.__stdout__)
        return None

    def _run_select(cat):
        cmd = ['python3', str(script), 'select', '--agent', _EMOJI_AGENT_NAME, '--category', cat, '--seed', str(time.time_ns())]
        cp = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, timeout=10)
        if cp.returncode != 0:
            print(f'[clawbot-hook] emoji select failed cat={cat!r} rc={cp.returncode}: {cp.stderr.strip()}', file=sys.__stdout__)
            return None
        data = json.loads(cp.stdout or '{}')
        path = data.get('path') if data.get('ok') else None
        if path and Path(path).exists():
            return path
        return None

    try:
        path = _run_select(category)
        if path:
            return path
        print(f'[clawbot-hook] emoji select no path cat={category!r}', file=sys.__stdout__)
        return None
    except Exception as e:
        print(f'[clawbot-hook] emoji select error: {type(e).__name__}: {e}', file=sys.__stdout__)
    return None

def _parse_emoji_token(text):
    raw = str(text or '')
    m = _EMOJI_TOKEN_RE.match(raw)
    if m:
        cat = (m.group(1) or 'happy').strip()
        return cat, ''
    return None

def _visible_dialog_only(text):
    """Last-mile guard for clawbot: send only user-visible dialogue.

    GA core and memory stay untouched; this only sanitizes text right before
    WeChat send_text so summaries/tool logs/working memory cannot leak to chat.
    """
    raw = str(_maybe_parse_text_payload(text) or '')
    # Legacy persisted turns may contain an OpenAI-style text content array that
    # was stringified and then escaped again, e.g.
    # "[{\\'type\\': \\'text\\', \\'text\\': \"...\"}]".  If normal parsing did
    # not unwrap it, remove only that outer payload shell before applying the
    # regular SYSTEM/summary cleanup below.
    if "\\'type\\'" in raw and "\\'text\\'" in raw:
        probe = raw.replace("\\'", "'").replace('\\\\n', '\n').replace('\\n', '\n')
        m_payload = re.match(r'''(?is)^\s*\[\s*\{\s*'type'\s*:\s*'text'\s*,\s*'text'\s*:\s*(["'])(?P<body>.*)\1\s*\}\s*\]\s*$''', probe)
        if m_payload:
            raw = m_payload.group('body')
    s = html.unescape(raw)
    # Hard stop for agent-shell dumps.  When the upstream returns the whole
    # WORKING MEMORY / memory / persona prompt as "assistant text", it is safer
    # to send nothing than to try to salvage dialogue from the prompt body.  If
    # a real final-response marker exists after the dump, keep only that tail.
    if re.search(r'(?im)^\s*### \[WORKING MEMORY\]', s) and re.search(r'(?im)^\s*(?:cwd\s*=|\[Memory\]|\[CONSTITUTION\]|\.\./memory/)', s):
        m_shell_tail = _DIALOG_MARK_RE.search(s)
        if m_shell_tail:
            s = m_shell_tail.group(1).lstrip('：: \t')
        else:
            return ''
    # First remove structural internal blocks before any "正文/Response" tail
    # extraction.  Working-memory/history may contain old user text with those
    # words; if we extract first, the block header is lost and the internal body
    # can leak as WeChat dialogue.
    s = _LEAK_BLOCK_RE.sub('', s)
    if not s.strip():
        return ''
    # Drop internal title-generation tasks and strip tool-usage hints that may
    # be injected by upstream adapters before the real human message. These are
    # not user-visible clawbot dialogue and must never persist as xinghui:main
    # chat history.
    if _INTERNAL_TITLE_TASK_RE.search(s):
        return ''
    s = _INTERNAL_VISION_HINT_RE.sub('', s).lstrip()
    s = _strip_client_file_hint_prefix(s)
    if _INTERNAL_TITLE_TASK_RE.search(s):
        return ''
    # Some persisted user turns are stringified multimodal text payloads whose
    # text begins with injected SYSTEM/time/output-format blocks followed by
    # the real human message.  Drop only those bracketed SYSTEM preambles and
    # keep the trailing dialogue, instead of deleting the whole user turn.
    s = re.sub(r'(?is)^(?:\s*【SYSTEM[^】]*】\s*)+', '', s)
    # Keep mid-strip in sync with session_history.visible_dialog_only: strip
    # whole internal marker lines only (incl. proactive 【上下文】/【话题】).
    # Never bare-word 上下文 — dual over-clean previously ate real body text.
    _prev = None
    while _prev != s:
        _prev = s
        s = re.sub(
            r'(?is)【(?:随机自主唤醒|私聊主动发起[^】]*|可选日常来源|私下意图|同期群聊原文|上下文|话题|SYSTEM\s*最近\d+分钟微信群聊现场)[^】]*】[^\n]*(?:\n|$)',
            '',
            s,
        ).lstrip()
    s = _prefer_dialog_marked_tail(s)
    m = _DIALOG_MARK_RE.search(s)
    if m:
        s = m.group(1).lstrip('：: \t')
    s = _LEAK_BLOCK_RE.sub('', s)
    s = _strip_task_done_trailing_note(s)
    kept = []
    skipping_json = 0
    for raw in s.splitlines():
        line = raw.rstrip()
        st = line.strip()
        if not st:
            if kept and kept[-1] != '':
                kept.append('')
            continue
        if skipping_json > 0:
            skipping_json += st.count('{') - st.count('}')
            if skipping_json <= 0:
                skipping_json = 0
            continue
        if st.startswith(('{', '[')) and _JSON_TOOL_KEY_RE.search(st):
            depth = st.count('{') + st.count('[') - st.count('}') - st.count(']')
            skipping_json = max(depth, 0)
            continue
        if _LEAK_LINE_RE.search(st):
            continue
        kept.append(line)
    s = '\n'.join(kept).strip()
    _prev = None
    while _prev != s:
        _prev = s
        s = _LEAK_TAIL_RE.sub('', s).rstrip()
    s = s.strip()
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s


_IMAGE_FILE_RE = re.compile(r'\[用户发送文件:\s*([^\]\n]+?)\s*\]')
_IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}


def _build_direct_image_vision_block(prompt: str) -> str:
    """Describe images downloaded by official clawbot media handling.

    Official frontends.wechatapp downloads private-chat media to local temp files
    and turns them into ``[用户发送文件: path]`` text.  The group image bridge scans
    WeChat process memory, which is unsuitable as a private-chat fallback.  Here
    we use the already-downloaded file path directly and reuse the same OCR/vision
    describer/cache.
    """
    if not prompt:
        return ''
    paths = []
    seen = set()
    for m in _IMAGE_FILE_RE.finditer(prompt):
        raw = (m.group(1) or '').strip().strip('"\'')
        if not raw:
            continue
        p = Path(raw)
        if p.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        key = str(rp)
        if key in seen:
            continue
        seen.add(key)
        if rp.exists() and rp.is_file():
            paths.append(rp)
    if not paths:
        return ''
    try:
        from plugins.local_frontends import wechat_image_vision_bridge as vib
        cfg = vib._cfg()
        if cfg.get('enable') is False:
            return ''
        vib._ensure_dirs()
        descs = []
        for p in paths[:int(cfg.get('top_recent') or 6)]:
            desc = vib._desc_for(p, cfg)
            if desc:
                descs.append(desc)
        if not descs:
            return ''
        body = '\n'.join(f'- {d}' for d in descs)
        return '\n【本轮私聊图片内容（直接来自clawbot已下载图片的OCR文字+vision描述）】\n' + body
    except Exception as e:
        print(f'[clawbot-image-vision] direct image describe failed: {type(e).__name__}: {e}', file=sys.__stdout__)
        return ''

_wx = None
_app = None
_patched = False

def _patch_official(wx, app):
    global _patched, SHARED_HOOK_APP
    if _patched:
        return
    SHARED_HOOK_APP = app
    orig_send_text = wx.WxBotClient.send_text
    orig_send_image = wx.WxBotClient.send_image
    orig_on_message = wx.on_message
    orig_put_task = wx.agent.put_task

    def send_image_patched(self, to_user_id, file_path, context_token=''):
        """Final emoji safety gate.

        Official on_message scans [FILE:path] after send_text and may call
        send_image directly.  If that path points to the local emoji library,
        do not bind to the concrete filename; re-select a random image from
        the parent category.  Non-emoji images keep the official behavior.
        """
        try:
            ref_path = Path(str(file_path or '')).resolve()
            rel = ref_path.relative_to(_EMOJI_ROOT)
            category = rel.parts[0] if rel.parts else ''
        except Exception:
            category = ''
        if category:
            fpath = _select_emoji_path(category)
            if fpath:
                print(f'[clawbot-hook] randomized direct emoji send_image: cat={category!r} original={file_path!r} selected={fpath!r}', file=sys.__stdout__)
                return orig_send_image(self, to_user_id, fpath, context_token=context_token)
            print(f'[clawbot-hook] emoji send_image randomize failed; fallback original: cat={category!r} path={file_path!r}', file=sys.__stdout__)
        return orig_send_image(self, to_user_id, file_path, context_token=context_token)

    def send_text_patched(self, to_user_id, text, context_token=''):
        app.bot = self
        app.last_uid = to_user_id or app.last_uid
        app.last_context_token = context_token or app.last_context_token

        # First normalize GA/model payload into user-visible dialogue, then let
        # frontend plugins (message bubble splitter, emoji labels, etc.) work
        # on clean dialogue.  If we split before this step, a Python/JSON repr
        # containing escaped newlines and internal blocks can be cut into
        # fragments, making both leak filtering and bubble splitting unstable.
        print(f'[DEBUG] send_text_patched BEFORE clean: text={text[:500]!r}', file=sys.__stdout__)
        chunks = _visible_dialog_only(text)
        print(f'[DEBUG] send_text_patched AFTER clean: chunks={chunks[:500] if isinstance(chunks, str) else chunks}', file=sys.__stdout__)
        for fn in list(app.hooks.get('before_send_text', [])):
            try:
                r = fn(to_user_id, chunks, context_token)
                if r is not None:
                    chunks = r
            except Exception as e:
                print(f'[clawbot-hook] before_send_text error in {getattr(fn, "__module__", "?")}: {type(e).__name__}: {e}', file=sys.__stdout__)
        def _send_one_payload(payload):
            # Final safety gate after all before_send_text plugins.  Bubble
            # splitters may return a list of payloads; each payload must still
            # go through the same cleanup path so internal blocks
            # can never become an individual outgoing bubble.
            raw_payload = str(payload or '').strip()
            print(f'[DEBUG] _send_one_payload BEFORE clean: payload={raw_payload[:500]!r}', file=sys.__stdout__)
            clean = _visible_dialog_only(payload).strip()
            print(f'[DEBUG] _send_one_payload AFTER clean: clean={clean[:500]!r} empty={not clean}', file=sys.__stdout__)
            if not clean:
                return None
            legacy_image = _LEGACY_IMAGE_REF_RE.match(clean)
            if legacy_image:
                fpath = legacy_image.group(1).strip()
                try:
                    ret = self.send_image(to_user_id, fpath, context_token=context_token)
                    if _wechat_send_ok(ret):
                        _append_l4_wechat('assistant', clean, to_user_id)
                    else:
                        print(f'[clawbot-hook] legacy image send not ok; skip history persist ret={ret!r}', file=sys.__stdout__)
                    print(f'[clawbot-hook] sent legacy image ref: {fpath}', file=sys.__stdout__)
                    return ret
                except Exception as e:
                    print(f'[clawbot-hook] send legacy image ref failed: {type(e).__name__}: {e}; path={fpath!r}', file=sys.__stdout__)
                    return None
            if _INTERNAL_FILE_REF_RE.match(clean):
                print(f'[clawbot-hook] suppressed internal file ref bubble: {clean[:160]!r}', file=sys.__stdout__)
                return None
            emoji = _parse_emoji_token(clean)
            if emoji:
                fpath = _select_emoji_path(emoji[0])
                if fpath:
                    try:
                        ret = self.send_image(to_user_id, fpath, context_token=context_token)
                        if _wechat_send_ok(ret):
                            _append_l4_wechat('assistant', clean, to_user_id)
                        else:
                            print(f'[clawbot-hook] emoji image send not ok; skip history persist ret={ret!r}', file=sys.__stdout__)
                        print(f'[clawbot-hook] sent emoji image: {fpath}', file=sys.__stdout__)
                        return ret
                    except Exception as e:
                        print(f'[clawbot-hook] send emoji image failed: {type(e).__name__}: {e}', file=sys.__stdout__)
                        ret = orig_send_text(self, to_user_id, clean, context_token=context_token)
                        if _wechat_send_ok(ret):
                            _append_l4_wechat('assistant', clean, to_user_id)
                        else:
                            print(f'[clawbot-hook] emoji fallback send not ok; skip history persist ret={ret!r}', file=sys.__stdout__)
                        return ret
            
            # === Physical keyboard send fallback (main path) ===
            # Try physical send first, fallback to API if it fails
            try:
                import subprocess
                def _phys_run(cmd, timeout=10):
                    try:
                        env = os.environ.copy()
                        env['DISPLAY'] = ':0'
                        return subprocess.run(cmd, capture_output=True, text=True, 
                                            timeout=timeout, check=False, env=env)
                    except Exception as e:
                        class FR:
                            returncode = 1
                            stdout = ''
                            stderr = str(e)
                        return FR()
                
                def _phys_find_window(title):
                    for _ in range(2):
                        r = _phys_run(['wmctrl', '-lx'], 8)
                        if r.returncode == 0:
                            for line in r.stdout.strip().splitlines():
                                if title in line:
                                    parts = line.split(None, 4)
                                    if parts:
                                        return parts[0]
                        time.sleep(0.3)
                    return None
                
                def _phys_send(win_id, text):
                    # Activate
                    _phys_run(['wmctrl', '-ia', win_id], 5)
                    time.sleep(0.2)
                    # Get geometry
                    geom = _phys_run(['xdotool', 'getwindowgeometry', '--shell', win_id], 5).stdout
                    vals = {}
                    for l in geom.splitlines():
                        if '=' in l:
                            k, v = l.split('=', 1)
                            vals[k] = int(v) if v.isdigit() else v
                    # Positions
                    x = int(vals.get('X', 0) + vals.get('WIDTH', 900) * 0.50)
                    y = int(vals.get('Y', 0) + vals.get('HEIGHT', 700) * 0.93)
                    sx = int(vals.get('X', 0) + vals.get('WIDTH', 900) * 0.88)
                    sy = int(vals.get('Y', 0) + vals.get('HEIGHT', 700) * 0.955)
                    # Click input
                    _phys_run(['xdotool', 'mousemove', str(x), str(y), 'click', '1'], 5)
                    time.sleep(0.1)
                    # Clipboard
                    xclip = '/home/aimcafe/GA_Instances/xinghui-ga/plugins/wechat_multi_chat_rust/vendor/xclip/xclip/usr/bin/xclip'
                    if not Path(xclip).exists():
                        xclip = 'xclip'
                    p = subprocess.Popen([xclip, '-selection', 'clipboard', '-i'],
                                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, env={'DISPLAY': ':0'})
                    p.stdin.write(text.encode('utf-8'))
                    p.stdin.close()
                    time.sleep(0.1)
                    # Paste
                    _phys_run(['xdotool', 'key', 'ctrl+v'], 5)
                    time.sleep(0.1)
                    # Send
                    _phys_run(['xdotool', 'mousemove', str(sx), str(sy), 'click', '1'], 5)
                    return True
                
                # Try physical send with "无言" window (yanyan's private chat)
                wid = _phys_find_window('无言')
                if wid:
                    if _phys_send(wid, clean):
                        print(f'[clawbot-hook] physical send OK to 无言', file=sys.__stdout__)
                        _append_l4_wechat('assistant', clean, to_user_id)
                        try:
                            visible_prompt = app.pending_visible_user_prompt or app.last_incoming_text
                            if visible_prompt:
                                history_prompt = _build_clawbot_history_user_prompt(visible_prompt)
                                _replace_last_user_history(wx.agent, history_prompt)
                            persist_frontend_session(wx.agent, _SESSION_KEY)
                        except Exception as e:
                            print(f'[clawbot-session] persist warning: {type(e).__name__}: {e}', file=sys.__stdout__)
                        return {'ret': 0, 'msg': 'physical_send_ok'}
                    else:
                        print(f'[clawbot-hook] physical send failed, fallback to API', file=sys.__stdout__)
                else:
                    print(f'[clawbot-hook] 无言 window not found, fallback to API', file=sys.__stdout__)
            except Exception as e:
                print(f'[clawbot-hook] physical send error: {type(e).__name__}: {e}, fallback to API', file=sys.__stdout__)
            # === End physical send ===
            
            ret = orig_send_text(self, to_user_id, clean, context_token=context_token)
            if not _wechat_send_ok(ret):
                print(f'[clawbot-session] send_text not ok; skip L4/session persist ret={ret!r}', file=sys.__stdout__)
                return ret
            _append_l4_wechat('assistant', clean, to_user_id)
            try:
                visible_prompt = app.pending_visible_user_prompt or app.last_incoming_text
                if visible_prompt:
                    history_prompt = _build_clawbot_history_user_prompt(visible_prompt)
                    _replace_last_user_history(wx.agent, history_prompt)
                persist_frontend_session(wx.agent, _SESSION_KEY)
            except Exception as e:
                print(f'[clawbot-session] persist after send_text failed: {type(e).__name__}: {e}', file=sys.__stdout__)
            return ret

        if isinstance(chunks, (list, tuple)):
            ret = None
            for chunk in chunks:
                r = _send_one_payload(chunk)
                if r is not None:
                    ret = r
            return ret

        ret = _send_one_payload(chunks)
        if ret is None:
            print('[clawbot-hook] suppressed non-dialogue send_text payload', file=sys.__stdout__)
        return ret

    class _SerialTaskQueue:
        def __init__(self, dq, lock):
            self._dq = dq
            self._lock = lock
            self._released = False

        def _release_once(self):
            if self._released:
                return
            self._released = True
            try:
                self._lock.release()
                print('[clawbot-session] released serial turn lock', file=sys.__stdout__)
            except Exception as e:
                print(f'[clawbot-session] release serial turn lock failed: {type(e).__name__}: {e}', file=sys.__stdout__)

        def get(self, *args, **kwargs):
            try:
                item = self._dq.get(*args, **kwargs)
            except Exception:
                self._release_once()
                raise
            if isinstance(item, dict) and 'done' in item:
                self._release_once()
            return item

        def __getattr__(self, name):
            return getattr(self._dq, name)

    def put_task_patched(prompt, *args, **kwargs):
        uid = app.last_uid
        prompt = prompt or ''
        direct_image_block = _build_direct_image_vision_block(prompt)
        if direct_image_block:
            prompt = prompt.rstrip() + '\n' + direct_image_block
            print(f'[clawbot-image-vision] injected direct image block chars={len(direct_image_block)}', file=sys.__stdout__)
        visible_prompt = _visible_dialog_only(prompt or app.last_incoming_text or '')
        app.pending_visible_user_prompt = (visible_prompt or '').strip()
        try:
            frontend_instruction = str(get_prompt_config('runtime.frontend_instruction', '') or '')
            schedule_hint = str(get_prompt_config('runtime.schedule_hint', '') or '')
            legacy_template = get_wechat_entry_prompt(
                prompt='',
                frontend_instruction='{{frontend_instruction}}',
                schedule_hint='{{schedule_hint}}',
            )
            scene_prefix = format_prompt(
                'runtime.wechat_entry_prompt',
                legacy_template or '【SYSTEM 当前前端入口说明：{{frontend_instruction}}{{schedule_hint}}】\n',
                frontend_instruction=frontend_instruction,
                schedule_hint=schedule_hint,
            )
        except Exception:
            scene_prefix = ''
        prompt = scene_prefix + (prompt or '')
        print('[clawbot-session] waiting serial turn lock', file=sys.__stdout__)
        app.serial_turn_lock.acquire()
        print('[clawbot-session] acquired serial turn lock', file=sys.__stdout__)
        try:
            # Clawbot and API share the same frontend turn lifecycle for
            # xinghui:main: restore persistent backend history, run the canonical
            # before_put_task prompt assembly hooks, enqueue native GA, and persist
            # after the visible assistant send.  Per-turn side context is not
            # separately persisted.
            dq, new_prompt = enqueue_frontend_task(
                wx.agent,
                orig_put_task,
                prompt,
                session_key=_SESSION_KEY,
                uid=uid,
                hook_app=app,
                put_args=args,
                put_kwargs={**kwargs, 'source': 'clawbot_private'},
            )
            # Do not persist immediately after enqueueing: native backend.history
            # has not yet received this turn's assistant answer. Persisting here
            # can overwrite the on-disk clawbot session with a history that is one
            # turn behind. send_text_patched persists after visible assistant send.
            return _SerialTaskQueue(dq, app.serial_turn_lock)
        except Exception:
            try:
                app.serial_turn_lock.release()
                print('[clawbot-session] released serial turn lock after put_task error', file=sys.__stdout__)
            except Exception as e:
                print(f'[clawbot-session] release after put_task error failed: {type(e).__name__}: {e}', file=sys.__stdout__)
            raise

    def on_message_patched(bot, msg):
        """Debounce consecutive clawbot user messages per uid.

        Official frontends.wechatapp spawns one worker thread for every incoming
        message.  When the user sends a/b/c quickly, several workers can race on
        the single shared xinghui:main backend history and produce replies that
        answer stale adjacent pairs (ab/bc).  For clawbot we collapse a short
        burst from the same uid into one official on_message call: earlier texts
        are preserved as immediate context, while the final text is explicitly
        marked as the one to answer.
        """
        app.bot = bot
        uid = msg.get('from_user_id', '')
        ctx = msg.get('context_token', '')
        try:
            text = bot.extract_text(msg).strip()
        except Exception:
            text = ''

        app.last_uid = uid
        app.last_context_token = ctx
        app.last_incoming_text = _visible_dialog_only(text) or text
        # Keep proactive private-chat target fresh.  wx_plus_proactive registers
        # after_message to remember the last real clawbot private uid/context_token;
        # without emitting it here, group-origin proactive events may send with a
        # stale token and fail at clawbot sendmessage (ret=-2).
        app.emit('after_message', msg)
        if text:
            _append_l4_wechat('user', text, uid)

        # Shared group-awareness command: only toggles live group side context.
        # Legacy scene-switch commands are no longer clawbot mode switches; scene/output
        # is controlled by the configurable entry prompt and normal conversation.
        try:
            import wx_plus_runtime_context as rc
            group_cmd = rc.parse_group_awareness_command(text)
        except Exception:
            group_cmd = None
        if group_cmd is not None and uid:
            try:
                import wx_plus_runtime_context as rc
                enabled = rc.set_group_awareness_enabled(bool(group_cmd), source='clawbot')
                reply = rc.group_awareness_reply(enabled)
            except Exception as e:
                enabled = bool(group_cmd)
                reply = '已开启群聊感知/参与。' if enabled else '已关闭群聊感知/参与。'
                print(f'[clawbot-group-awareness] command failed: {e}', file=sys.__stdout__)
            try:
                bot.send_text(uid, reply, context_token=ctx)
            except Exception as e:
                print(f'[clawbot-group-awareness] send reply failed: {e}', file=sys.__stdout__)
            return None

        # Media/non-text messages must keep official behaviour; only text bursts
        # are debounced here.
        if not uid or not text:
            return orig_on_message(bot, msg)

        delay = float(os.environ.get('WECHAT_PLUS_TEXT_DEBOUNCE_SECONDS', '1.2'))
        with app.debounce_lock:
            state = app.debounce_by_uid.get(uid)
            if state is None:
                state = {'seq': 0, 'items': [], 'last_msg': msg}
                app.debounce_by_uid[uid] = state
            state['seq'] += 1
            seq = state['seq']
            state['items'].append((text, msg))
            state['last_msg'] = msg
            state['last_ctx'] = ctx
            print(f'[clawbot-debounce] queued uid={uid} seq={seq} n={len(state["items"])} text={text[:60]!r}', file=sys.__stdout__)

        def debounced_worker(worker_uid, worker_seq):
            time.sleep(delay)
            with app.debounce_lock:
                state = app.debounce_by_uid.get(worker_uid)
                if not state or state.get('seq') != worker_seq:
                    print(f'[clawbot-debounce] superseded uid={worker_uid} seq={worker_seq}', file=sys.__stdout__)
                    return
                items = list(state.get('items') or [])
                final_msg = state.get('last_msg') or msg
                final_ctx = state.get('last_ctx') or ''
                app.debounce_by_uid.pop(worker_uid, None)

            raw_texts = [t for t, _m in items if t]
            texts = [(_visible_dialog_only(t) or t).strip() for t in raw_texts]
            texts = [t for t in texts if t]
            if not texts:
                return orig_on_message(bot, final_msg)
            final_text = texts[-1]
            if len(texts) == 1:
                merged_text = final_text
            else:
                prev = '\n'.join(f'{i+1}. {t}' for i, t in enumerate(texts[:-1]))
                merged_text = format_prompt(
                    'wechat_media_batch.merged_previous_messages',
                    '【刚才对方连续发了几条消息；这些是同一轮里的前文，请理解但不要逐条分别回复。】\n{{previous_messages}}\n\n【最后一条/本轮真正需要直接回复的是：】\n{{final_text}}',
                    previous_messages=prev,
                    final_text=final_text,
                )
            app.last_uid = worker_uid
            app.last_context_token = final_ctx
            app.last_incoming_text = merged_text
            print(f'[clawbot-debounce] flush uid={worker_uid} count={len(texts)} final={final_text[:80]!r}', file=sys.__stdout__)

            orig_extract = bot.extract_text
            def extract_text_once(m):
                if m is final_msg:
                    return merged_text
                return orig_extract(m)

            bot.extract_text = extract_text_once
            try:
                return orig_on_message(bot, final_msg)
            finally:
                bot.extract_text = orig_extract

        threading.Thread(target=debounced_worker, args=(uid, seq), daemon=True).start()
        return None

    wx.WxBotClient.send_text = send_text_patched
    wx.WxBotClient.send_image = send_image_patched
    wx.agent.put_task = put_task_patched
    wx.on_message = on_message_patched
    _patched = True

def create_shared_hook_app(wx_module=None):
    """Build the canonical xinghui frontend HookApp used by all display frontends.

    The returned app has the same local frontend plugins installed.  It does not
    patch the official WeChat frontend and does not start the bot, so API can use
    it safely in its own process as the same prompt/session assembler.
    """
    if wx_module is None:
        import frontends.wechatapp as wx_module
    app = HookApp(wx_module)
    _install_plugins(app)
    return app


def ensure_initialized_for_import():
    """Initialize official frontends.wechatapp + clawbot hooks when imported.

    This is used by the Rust private-chat bridge.  It deliberately avoids the
    deleted custom/multi-agent layer and only patches the official frontend in
    memory for this process.
    """
    global _wx, _app, agent, on_message
    if _wx is None:
        import frontends.wechatapp as wx
        _wx = wx
        _app = create_shared_hook_app(wx)
        _patch_official(wx, _app)
        agent = wx.agent
        on_message = wx.on_message
    return _wx, _app

def __getattr__(name):
    if name in {'agent', 'on_message'}:
        ensure_initialized_for_import()
        return globals()[name]
    raise AttributeError(name)

def main():
    wx, app = ensure_initialized_for_import()

    _do_relogin = '--relogin' in sys.argv
    try:
        _lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock.bind(('127.0.0.1', 19531))
    except OSError:
        print('[WeChat] Another instance running, exiting.')
        sys.exit(1)
    _logf = open(ROOT / 'temp' / 'wechatapp.log', 'a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = _logf
    print(f'[NEW] Process starting {time.strftime("%m-%d %H:%M")} [native-hook]')
    bot = wx.WxBotClient()
    app.bot = bot
    if _do_relogin or not bot.token:
        if not sys.stdout.isatty():
            print('[Bot] no token and not interactive, exit.')
            sys.exit(1)
        sys.stdout = sys.stderr = sys.__stdout__
        bot.login_qr()
        sys.stdout = sys.stderr = _logf
    _install_plugins(app)
    threading.Thread(target=wx.agent.run, daemon=True).start()
    # API is now a standalone display frontend (xinghui-ga-api.service) that
    # builds the same HookApp prompt assembler via create_shared_hook_app().
    # Do not embed the API server in the clawbot process: the old embedded
    # bind_external_agent/start_background path was removed when sessions were
    # unified, and keeping it here only creates a stale failing startup branch.
    print('[API] standalone OpenAI API service is used; embedded clawbot API disabled', file=sys.__stdout__)
    print(f'WeChat Bot 已启动 (bot_id={bot.bot_id})', file=sys.__stdout__)
    try:
        bot.run_loop(wx.on_message)
    except wx.AuthExpired:
        print('[Bot] token expired, exit.', file=sys.__stdout__)
        sys.exit(2)


if __name__ == '__main__':
    main()
