"""Shared persistent session-history helpers for local frontends.

This module is the single frontend-layer owner of the on-disk
``wechat_session_state.json`` backend_history format.  Clawbot and the
OpenAI-compatible API must use the same functions so a session key such as
``char:main`` means the same native GA backend session regardless of entry.

It intentionally does not modify GA core/session classes.  It only normalizes
frontend persistence to GA native list-of-text-block content and strips old
frontend pollution (summary/tool/system wrappers) before restore/save.
"""
from __future__ import annotations

import ast
import html
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from prompt_config import get_prompt_config as _get_prompt_config
except Exception:
    def _get_prompt_config(_path, default=None):
        return default
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SESSION_STATE_PATH = ROOT / 'plugins' / 'local_frontends' / 'runtime' / 'state' / 'wechat_session_state.json'
_SESSION_LOCK = threading.RLock()

# Shared-session context budget (xinghui:main etc). Full detail stays in L4.
# Soft threshold: when backend_history exceeds this many items, compress far history.
# Default ~55 ≈ 近20轮对话(≈40条) + 1条角色视角摘要 + 少量缓冲，再触发压缩。
_HISTORY_SOFT_LIMIT = int(os.environ.get('GA_SHARED_HISTORY_SOFT_LIMIT', '55'))
# Hard keep: after compress, retain at most this many recent items (+ 1 summary prepended).
# 40 items ≈ 20 complete user/assistant turns of full dialogue.
_HISTORY_HARD_KEEP = int(os.environ.get('GA_SHARED_HISTORY_HARD_KEEP', '40'))
# Minimum items before intentional compress is allowed past shrink guard.
_HISTORY_MIN_FOR_COMPRESS = 30
_L4_DIR = ROOT / 'memory' / 'L4_raw_sessions'
_COMPRESS_MARKER = '【历史压缩摘要'

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
    # If the payload is a successfully parsed structured list/dict, it is an
    # OpenAI/Claude-style content envelope rather than human-visible prose.  In
    # that case only extracted text blocks are visible; pure tool_use/tool_result
    # envelopes must become empty instead of falling back to their raw repr.
    if raw[0] in '[{':
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(raw)
            except Exception:
                continue
            if isinstance(parsed, (dict, list, tuple)):
                return _extract_text_payload(parsed)
    return s


_LEAK_BLOCK_RE = re.compile(
    r'(?is)'
    # Paired internal tags: remove the tag and everything inside.
    r'<summary\b[^>]*>.*?</summary>\s*|'
    r'<thought\b[^>]*>.*?</thought>\s*|'
    r'<thinking\b[^>]*>.*?</thinking>\s*|'
    r'<analysis\b[^>]*>.*?</analysis>\s*|'
    r'<tool_call\b[^>]*>.*?</tool_call>\s*|'
    r'<tool_response\b[^>]*>.*?</tool_response>\s*|'
    # GA runtime wrappers around the final visible response. These are
    # transport/debug shells, not dialogue.
    r'\*\*\s*LLM\s+Running\b.*?\*\*\s*|'
    r'`{3,}\s*\[Info\]\s*Final response to user\.\s*`{3,}\s*|'
    r'`{3,}\s*\[/?Info\][^`]*?`{3,}\s*|'
    # Full GA tool transcript emitted inside a final assistant payload.  Remove
    # the complete structured runtime block (tool header + args fence + action /
    # stdout fence) and keep the natural language tail that follows.  Some
    # adapters expose only the tool header + args fence in the final payload;
    # that is still runtime transcript, never dialogue.
    r'🛠️?\s*Tool:\s*`[^`]+`.*?\[Stdout\]\s*(?:`{3,}[^\n`]*\n)?.*?\n`{3,}\s*|'
    r'🛠️?\s*Tool:\s*`[^`]+`.*?(?=^\s*(?:最终回复|Final response|assistant final|正文\s*[:：]|\[REPLY\])\s*|\Z)|'
    r'^\s*🛠️\s*[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?\s*\([^\n]*\)\s*(?:\n|$)|'
    # GA/clawbot injected prompt block accidentally echoed as assistant text.
    # Marker-based only: remove from [USER]/SYSTEM preamble through the explicit
    # response boundary, not by guessing directive semantics.
    r'^\s*\[USER\]\s*[:：].*?(?:\(Begin your response\)|^\s*Assistant\s*[:：]|^\s*最终回复\s*[:：]|^\s*正文\s*[:：])\s*|'
    r'^\s*[【\[]\s*SYSTEM\b.*?(?:\(Begin your response\)|^\s*Assistant\s*[:：]|^\s*最终回复\s*[:：]|^\s*正文\s*[:：])\s*|'
    r'^\s*[【\[]\s*SYSTEM\b[^\n\]】]*(?:\][^\n]*)?[\]】]\s*\n?|'
    # Standalone frontend entry shell / fact-memory shell that may prefix a user
    # turn and leak into backend_history when no explicit response marker exists.
    r'^\s*[【\[]\.[^\]\n】]+\.[^\]\n】]*[\]】]\s*(?:\n|$)|'
    r'^\s*[【\[]\s*SYSTEM\s+与用户有关的已知事实记忆.*?(?=^\s*(?:我|你|他|她|想|要|能|会|不|别|请|帮|为什么|怎么|是否|是不是|今天|现在|刚刚|昨天|明天|嗯|啊|哦|诶|哈|嗨|早|晚)|\Z)|'
    # Whole internal thought/tool/planning blocks are not dialogue.  If no explicit final
    # marker exists in the same payload, suppress them structurally instead of
    # deleting only the marker line and leaking the body after a blank line.
    r'\[THOUGHT\].*?(?=^\s*(?:最终回复|Final response|assistant final|正文)\s*[:：]|\Z)|'
    r'^\s*(?:#{1,6}\s*)?(?:\[\s*)?(?:THINKING|Thought\s*Process|Analysis|Reasoning|Plan|Draft\s*response)(?:\s*\])?\s*[:：]?.*?(?=^\s*(?:最终回复|Final response|assistant final|正文|Response)\s*[:：]|\Z)|'
    r'^\s*(?:\*{1,3}\s*)?[【\[]?\s*(?:CRITICAL\s+INSTRUCTION)\b.*?(?=^\s*(?:最终回复|Final response|assistant final|正文|Response)\s*[:：]|\Z)|'
    r'^\s*(?:\*{1,3}\s*)?[【\[]?\s*(?:输出\s*格式|回复\s*格式|本次\s*输出\s*要求).*?(?=^\s*(?:最终回复|Final response|assistant final|正文|Response)\s*[:：]|\Z)|'
    r'^\s*### \[WORKING MEMORY\].*?(?:</key_info>\s*|\Z)|'
    # API/agent-shell prompt snapshots can be persisted as a single user/context
    # turn after an upgrade or frontend crash.  They contain earlier_context,
    # history, Current turn and key_info blocks; none of that is visible dialogue
    # and it must not be restored into char:main.
    r'^\s*<earlier_context>.*?</earlier_context>\s*|'
    r'^\s*<history>.*?</history>\s*|'
    r'^\s*<key_info>.*?</key_info>\s*|'
    r'^\s*Current\s+turn\s*:\s*\d+\s*|'
    r'^\s*### \[(?:PLANNING|ACTIONS)\].*?(?=^\s*(?:### \[|\[USER\]|\[Agent\]|<summary|正文\s*[:：]|Final response|最终回复)|\Z)|'
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
    r'`{3,}\s*\[/?Info\]|'
    r'\[Info\]\s*Final response to user\.?|'
    r'(?:analysis|thinking|tool_use|tool_result|tool_call|code_run)\s*[:=：(]|'
    r'\[(?:THOUGHT|WORKING MEMORY|SYSTEM|SYSTEM TIPS|DANGER|TOOL|TOOL_CALL|TOOL_RESULT)\]|'
    r'### \[(?:WORKING MEMORY|PLANNING|ACTIONS|TOOL|TOOL_CALL|TOOL_RESULT)\]|'
    r'(?:</?(?:earlier_context|history|key_info)>|If you need to show files to user|必须在回复文本中包含|正在读取记忆或SOP文件|working key_info updated|Please provide your next thought and action/response|\*\*CRITICAL:\*\*|CRITICAL:|MUST include|MUST output text|Do NOT wrap it in a code block|cwd\s*=\s*/|\[Memory\]|\[CONSTITUTION\]|\[RULES\])|'
    r'\b(?:Current\s+turn\s*:|Turn\s+\d+\b)|'
    r'\[FILE:[^\]]+\]|'
    r'\[Image:\s*source:\s*(?:/home|/tmp|/var|/mnt)/[^\]]+\]|'
    r'\d{4}-\d{2}-\d{2}\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b|'
    r'(?:^|\s)/(?:home|tmp|var|mnt)/[^\s\]]*(?:plugins/local_frontends/emojis|emojis)/[^\s\]]*\.(?:png|jpe?g|gif|webp)|'
    r'Last tool call failed|'
    r'(?:stdout|stderr|exit_code|recipient_name|tool_uses|inline_eval|save_to_file|switch_tab_id)\b|'
    r'\{\s*"(?:cwd|script|timeout|type|status|stdout|stderr|exit_code|recipient_name|tool_uses)"\s*:'
    r')'
)
_LEAK_TAIL_RE = re.compile(
    r'(?is)' +
    r'(?:' +
    # Structural task-completion markers only; the optional trailing note before
    # them is handled by _strip_task_done_trailing_note, keyed by marker position.
    r'[\[【(（]?\s*(?:任务\s*已\s*完成|task\s*completed)\s*[\]】)）]?[。.!！]*|' +
    # Untagged internal narration sometimes survives when an upstream layer has
    # already stripped <summary> tags.  Limit this to a final third-person
    # process note about the incoming user/media state; never remove ordinary
    # assistant dialogue in the middle of a message.
    str(_get_prompt_config('session_history.trailing_process_note_sender_pattern', r'(?:用户|对方)')) + r'发送[^\n。.!！?？]{0,80}(?:表情|图片|状态|消息|内容)[^\n。.!！?？]{0,120}(?:安抚|询问|回应|回复|表达|说明|处理)[^\n。.!！?？]{0,80}[。.!！?？]*' +
    r')\s*$'
)
_JSON_TOOL_KEY_RE = re.compile(r'"(?:recipient_name|tool_uses|tool_call|tool_result|stdout|stderr|exit_code|script|cwd|timeout|inline_eval|save_to_file|switch_tab_id|key_info|related_sop)"\s*:')
_DIALOG_MARK_RE = re.compile(r'(?is)(?:^|\n)\s*(?:={2,}\s*)?(?:\[Info\]\s*Final response to user\.?|Final response to user\.?|最终回复|assistant final|正文\s*[:：]|Final response|Response)(?:\s*={2,})?\s*[:：]?\s*(.+)$')
_INTERNAL_TITLE_TASK_RE = re.compile(
    r'(?is)\bBased on the chat history, give this conversation a name\b.*?\bThe name is:\s*$'
)
_INTERNAL_VISION_HINT_RE = re.compile(
    r'(?is)^\s*For image/file vision, use code_run with:.*?(?:\n\s*\n|$)'
)
_CLIENT_FILE_HINT_PREFIX_RE = re.compile(
    r'(?is)^\s*(?:(?:user|用户)\s*[：:]\s*)?If\s+you\s+need\s+to\s+show\s+files\s+to\s+user,\s*use\s+\[FILE:filepath\]\s+in\s+your\s+response\.\s*'
)
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
    """Return the visible FINAL_ANSWER payload, closed or last-open fallback.

    The model occasionally emits the opening transport sentinel but omits the
    closing one.  If we only handle closed pairs, the raw sentinel leaks into
    WeChat/history/life-memory.  Match the API frontend: prefer the last closed
    pair; otherwise whitelist text after the last opening sentinel and cut a
    stray close marker if present.
    """
    text = str(s or '')
    matches = list(_FINAL_ANSWER_RE.finditer(text))
    if matches:
        return matches[-1].group(1).strip()
    open_idx = text.rfind(_FINAL_ANSWER_OPEN)
    if open_idx < 0:
        return None
    tail = text[open_idx + len(_FINAL_ANSWER_OPEN):]
    close_idx = tail.find(_FINAL_ANSWER_CLOSE)
    if close_idx >= 0:
        tail = tail[:close_idx]
    return tail.strip()


def _strip_final_answer_markers(s: str) -> str:
    """Remove any residual transport sentinels from user-visible text."""
    return str(s or '').replace(_FINAL_ANSWER_OPEN, '').replace(_FINAL_ANSWER_CLOSE, '')

_CLAWBOT_RUNTIME_PREFIX_RE = re.compile(
    r'(?is)^\s*(?:[^\n]*[；;]\s*)?\[微信普通聊天入口\]\s*\n'
    r'(?:当前这一轮来自微信/clawbot[^\n]*\n)*'
    r'(?:\s*/(?:chat|home)\s*)?'
)
_CLAWBOT_INTERNAL_TRIGGER_RE = re.compile(
    # Whole-payload kill list only for pure internal triggers.
    # 【上下文】/【话题】 are often model-echoed prefixes on real dialogue —
    # never put them here (would dual-clean-kill the body). Strip line-only
    # in mid-strip below. Never bare-word 上下文 either.
    r'(?is)^\s*【(?:随机自主唤醒|私聊主动发起[^】]*|可选日常来源|私下意图|同期群聊原文|SYSTEM\s*最近\d+分钟微信群聊现场)[^】]*】'
)
_CLAWBOT_INTERNAL_ASSISTANT_RE = re.compile(
    r'(?is)^\s*(?:\[INFO\]\s*User provided a long prompt in a file\.\s*The file content is:\s*)?'
    r'【SYSTEM\s*最近\d+分钟微信群聊现场'
)
_INTERNAL_ASSISTANT_PREFIX_RE = re.compile(
    r'(?is)^\s*(?:\[User\]\s*)?Tool call `[^`]+` successfully completed\..*?'
    r'If your reply contains chat text, it must be exactly what the persona would say\.\s*'
    r'Wait for user response if necessary\.\s*No rambling\.\s*'
)
_INTERNAL_ASSISTANT_ONLY_RE = re.compile(
    r'(?is)^\s*(?:\[SUMMARY FORMAT\]|\[RESPONSE FORMAT\]|###\s*ACTION\s+PREDICTION|'
    r'Natural dialogue starting from the very first character|No plan to execute\.)'
)
_INTERNAL_ACTION_PREFIX_RE = re.compile(
    r'(?is)^\s*###\s*ACTION\s+PREDICTION.*?(?:`\s*)?(?=(?:用户|早|晚安|嗯|不|好|我|你|别|没|在|先|等))'
)
_INTERNAL_LONG_PROMPT_REF_RE = re.compile(
    # GA may write user_prompt_<id>.md or user_prompt_<pid>_<ts>.md
    r'(?is)^\s*(?:\[User\]\s*)?Long user prompt saved to\s+/[^\s]+/user_prompt_[\w.-]+\.md\.\s*Read and execute\.\s*(?:\[Model\]\s*)?$'
)
_MD_THOUGHT_HEADING_RE = re.compile(r'^\s*\*\*([A-Z][A-Za-z0-9 ,\'’:_/-]{2,90})\*\*\s*$')
_PLAIN_THOUGHT_HEADING_RE = re.compile(r'^\s*([A-Z][A-Za-z0-9 ,\'’:_/-]{2,90})\s*$')
_MD_THOUGHT_BODY_RE = re.compile(
    r'(?is)\b(?:I\'m|I am|I\s+need|I\s+will|my\s+(?:next\s+)?step|currently\s+focusing|'
    r'focus(?:ing)?\s+on|analyz(?:e|ing)|process(?:ing)?|refin(?:e|ing)|formulat(?:e|ing)|'
    r'ensur(?:e|ing)|prioritiz(?:e|ing)|request(?:ed)?|response|final\s+answer|test\s+integrity|'
    r'Reading\s+User\s+Prompt|file_read|utilizing\s+the\s+file_read\s+function)\b'
)

def _strip_leading_markdown_thought_sections(text: str) -> str:
    """Drop model-internal markdown/plain thought preludes while preserving final text.

    Some API calls can return English self-monitoring paragraphs like
    ``**Refining Conversational Flow**`` or ``Reading User Prompt`` before the
    actual reply.  Strip only leading English title + English/internal-looking
    body blocks.  If the whole response is made of such thought blocks, return
    an empty string so API/frontends can use their safe visible fallback instead
    of leaking them.
    """
    raw = str(text or '').strip()
    parts = [p.strip() for p in re.split(r'\n\s*\n', raw) if p.strip()]
    if len(parts) < 2:
        return text
    i = 0
    dropped = 0
    while i + 1 < len(parts):
        hm = _MD_THOUGHT_HEADING_RE.match(parts[i]) or _PLAIN_THOUGHT_HEADING_RE.match(parts[i])
        if not hm:
            break
        title = hm.group(1).lower()
        body = parts[i + 1]
        ascii_ratio = sum(1 for ch in body if ord(ch) < 128) / max(len(body), 1)
        title_hint = any(k in title for k in ('refin', 'flow', 'process', 'analysis', 'reason', 'think', 'response', 'test', 'focus', 'formulat', 'answer', 'reading', 'prompt'))
        if ascii_ratio < 0.70 or not (title_hint or _MD_THOUGHT_BODY_RE.search(body)):
            break
        i += 2
        dropped += 1
    if dropped and i < len(parts):
        return '\n\n'.join(parts[i:]).strip()
    if dropped and i >= len(parts):
        return ''
    return text

def _get_history_sanitize_config():
    try:
        from prompt_config import get_prompt_config
        return get_prompt_config('history_sanitize', default={}) or {}
    except Exception:
        return {}


def _strip_clawbot_runtime_wrapper(text: str) -> str:
    """Remove clawbot runtime prompt wrappers before persisting history."""
    s = str(text or '')
    if _CLAWBOT_INTERNAL_ASSISTANT_RE.search(s):
        return ''

    conf = _get_history_sanitize_config()
    markers = conf.get('runtime_entry_markers') or ['[微信普通聊天入口]']
    instruction_patterns = conf.get('runtime_entry_instruction_patterns') or [r'当前这一轮来自微信/clawbot[^\n]*\n']
    legacy_pattern = conf.get('legacy_runtime_mode_pattern') or ''

    for marker in markers:
        if marker and marker in s:
            s = s.rsplit(marker, 1)[1]
            for pat in instruction_patterns:
                if pat:
                    s = re.sub(r'(?is)^\s*' + pat, '', s)
            if legacy_pattern:
                s = re.sub(r'(?is)^' + legacy_pattern, '', s)
            s = s.lstrip()
            break
    else:
        s = _CLAWBOT_RUNTIME_PREFIX_RE.sub('', s).lstrip()

    if _CLAWBOT_INTERNAL_TRIGGER_RE.search(s):
        return ''
    return s

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
    r'/?(?:home|tmp|var|mnt)/[^\s\]]*(?:plugins/local_frontends/emojis|emojis)/[^\s\]]*\.(?:png|jpe?g|gif|webp)|'
    r'[^\n\r]*微信图片_[^\n\r]*\.(?:png|jpe?g|gif|webp)'
    r')\s*[。.!！?？]*\s*$'
)
_INTERNAL_MD_THOUGHT_TITLE_RE = re.compile(
    r'(?im)^\s*#{1,6}\s*\[(?:Thought\s*Process|Draft\s*response|Analysis|Reasoning|Thinking)\]\s*$'
)
_EMOJI_ROOT = (ROOT / 'data' / 'agents' / 'char' / 'emojis').resolve()
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
    """Keep only user-visible dialogue around transport final markers.

    Supported shapes:
    1. leading marker wrapper:
       ```
       [Info] Final response to user.
       ```
       visible text
    2. trailing empty marker wrapper:
       visible text
       `````
       [Info] Final response to user.
       `````
    3. inline marker:
       Final response to user: visible text
    4. GA multi-turn/tool wrapper:
       ... tool/runtime blocks ...
       [REPLY]
       visible text
       `````
       [Info] Final response to user.
       `````
    """
    text = str(s or '')
    final_answer = _extract_final_answer_tagged_tail(text)
    if final_answer is not None:
        return final_answer

    def _nonempty_without_fence(v: str) -> bool:
        return bool(re.sub(r'(?is)`{3,}\s*', '', str(v or '')).strip())

    def _strip_final_info_tail(v: str) -> str:
        return re.sub(
            r'(?is)\s*(?:`{3,}\s*)?\[Info\]\s*Final response(?:\s+to\s+user)?\.?\s*(?:`{3,}\s*)?$',
            '',
            str(v or ''),
        ).rstrip()

    # GA may aggregate all turn outputs into the final ``done`` payload.  In
    # that shape the only reliable frontend boundary is the last explicit
    # [REPLY] marker; everything before it is runtime/tool transcript, not user
    # visible dialogue.  Prefer this structural marker before generic cleanup.
    reply_matches = list(re.finditer(r'(?im)^\s*\[REPLY\]\s*$', text))
    if reply_matches:
        body = text[reply_matches[-1].end():]
        body = _strip_final_info_tail(body).strip()
        if _nonempty_without_fence(body):
            return body.rstrip()

    # Some GA final payloads have no [REPLY] marker but concatenate a tool
    # transcript followed by a Markdown separator and then the actual visible
    # answer, e.g. "Tool... [Stdout] ... ```\n\n###\n\nvisible text".
    # In that shape the separator is the last reliable structural boundary:
    # keep only the tail after the last separator when the head is clearly a
    # runtime/tool transcript.  This is not a semantic hard-rule; it removes
    # transport/runtime transcript before the human-visible dialogue.
    sep_matches = list(re.finditer(r'(?m)^\s*###\s*$', text))
    if sep_matches:
        head = text[:sep_matches[-1].start()]
        tail = text[sep_matches[-1].end():].strip()
        if _nonempty_without_fence(tail) and re.search(
            r'(?is)(🛠️|\bTool:\s*`|\[Action\]|\[Status\]|\[Stdout\]|Running\s+(?:bash|python)|📥\s*args|\b(?:ask_user|code_run|file_read|file_patch|file_write|web_scan|web_execute_js)\s*\(|\bCurrent\s+turn\s*:|\bTurn\s+\d+\b)',
            head,
        ):
            return tail.rstrip()

    # A fenced final-response marker at the very end is transport metadata, not
    # dialogue.  Remove it before any generic marker matching so the visible
    # head cannot be erased.
    text = re.sub(
        r'(?is)\s*`{3,}\s*\[Info\]\s*Final response to user\.?\s*`{3,}\s*$',
        '',
        text,
    ).rstrip()

    # Some GA final payloads have no [REPLY] marker but still concatenate
    # previous runtime/tool transcript before the final user-visible text.  The
    # runtime transcript is structurally introduced by an LLM Running fence; use
    # the last such fence as a boundary, then continue normal final-marker logic.
    runtime_matches = list(re.finditer(r'(?is)`{3,}\s*\*\*\s*LLM\s+Running\b.*?`{3,}', text))
    if runtime_matches:
        tail = text[runtime_matches[-1].end():].strip()
        if _nonempty_without_fence(tail):
            text = tail

    # Some model adapters may expose an English Markdown internal reasoning
    # wrapper inside the final text block, for example:
    #   #### [Thought Process]
    #   ...
    #   <summary>...</summary>
    #   visible reply
    # This is not user-visible dialogue.  Prefer the tail after the last
    # summary/thought closing tag; otherwise drop everything before the last
    # internal Markdown title so no chain-of-thought prose leaks.
    if _INTERNAL_MD_THOUGHT_TITLE_RE.search(text):
        close_matches = list(re.finditer(r'(?is)</(?:summary|thought|thinking|analysis)>', text))
        if close_matches:
            tail = text[close_matches[-1].end():].strip()
            if _nonempty_without_fence(tail):
                return tail.rstrip()
        title_matches = list(_INTERNAL_MD_THOUGHT_TITLE_RE.finditer(text))
        tail = text[title_matches[-1].end():].strip() if title_matches else ''
        if _nonempty_without_fence(tail):
            return tail.rstrip()
        return ''

    # Some API/model adapters can expose an internal thought prelude as a
    # transport-shaped prefix before the final visible text, for example:
    # "94>thought ...\n...\nSummary:\n<visible reply>".  Treat it as a
    # wrapper only when it starts at the payload head and has an explicit
    # Summary boundary after a thought marker; keep the tail as the dialogue.
    thought_summary = re.search(r'(?is)^\s*\d+\s*>\s*thought\b.*?(?:^|\n)\s*Summary\s*[:：]\s*(?P<body>.+)$', text)
    if thought_summary:
        body = thought_summary.group('body').strip()
        if _nonempty_without_fence(body):
            return body.rstrip()

    # Hard fail-safe for persisted adapter thought dumps without a clean final
    # marker.  The leaked incident looked like "94>thought ... CRITICAL
    # INSTRUCTION ... 傍晚余晖 ..." and had no reliable user-visible boundary.
    # Do not try to salvage prose from such payloads: suppress the whole turn so
    # a wrong-time/internal directive cannot become future shared session memory.
    if re.search(r'(?is)^\s*\d+\s*>\s*thought\b', text) and re.search(
        r'(?is)CRITICAL\s+INSTRUCTION|MUST\s+(?:include|output)|Do\s+NOT\s+wrap|Please\s+provide\s+your\s+next\s+thought|<summary\b|\[WORKING MEMORY\]',
        text,
    ):
        return ''

    # If an internal thought prelude has an explicit final-response marker under
    # another wording, prefer the marked tail.  This is narrower than the hard
    # suppression above and only fires for head-anchored thought dumps.
    thought_final = re.search(
        r'(?is)^\s*\d+\s*>\s*thought\b.*?(?:^|\n)\s*(?:Final response to user\.?|最终回复|assistant final|正文\s*[:：]|Response)\s*[:：]?\s*(?P<body>.+)$',
        text,
    )
    if thought_final:
        body = thought_final.group('body').strip()
        if _nonempty_without_fence(body):
            return body.rstrip()

    # If a natural-language head is followed by an unheaded runtime/tool
    # transcript fragment (for example JSON tail + [Action]/[Status]/[Stdout]),
    # keep the visible head and drop the transport tail as a whole.  Line-level
    # deletion is unsafe here because it removes markers but leaves stdout/body.
    runtime_tail = re.search(r'(?im)^\s*(?:\[AGENT ACTION\]|\[Action\]|\[Status\]|\[Stdout\])(?:\s|$)', text)
    if runtime_tail:
        head = text[:runtime_tail.start()].strip()
        head = _strip_final_info_tail(head).strip()
        if _nonempty_without_fence(head):
            return head.rstrip()
        # The whole payload is runtime/tool transcript; do not fall back to the
        # original text, otherwise the last-mile cleaner can leak [Action]/stdout.
        if runtime_tail.start() == 0:
            return ''

    # If the whole payload begins with a fenced marker, the visible dialogue is
    # after the closing fence.  Do not let a trailing empty fence consume it.
    m = re.match(r'(?is)^\s*`{3,}\s*\[Info\]\s*Final response to user\.?\s*`{3,}\s*(?P<body>.*)$', text)
    if m:
        body = m.group('body').lstrip('：: \t\r\n')
        if _nonempty_without_fence(body):
            return body.rstrip()

    matches = list(_DIALOG_MARK_RE.finditer(text))
    if not matches:
        return text
    last = matches[-1]
    tail = last.group(1).lstrip('：: \t\r\n')
    if _nonempty_without_fence(tail):
        return tail.rstrip()
    return text[:last.start()].rstrip()

def _select_emoji_path(category=''):
    category = (category or 'happy').strip() or 'happy'
    script = ROOT / 'plugins' / 'wechat_multi_chat_rust' / 'adapters' / 'emoji_library.py'
    if not script.exists():
        print(f'[clawbot-hook] emoji library missing: {script}', file=sys.__stdout__)
        return None

    def _run_select(cat):
        cmd = ['python3', str(script), 'select', '--agent', 'char', '--category', cat, '--seed', str(time.time_ns())]
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
    s = _strip_clawbot_runtime_wrapper(s)
    if _INTERNAL_ASSISTANT_ONLY_RE.search(s):
        return ''
    if _INTERNAL_LONG_PROMPT_REF_RE.search(s):
        return ''
    s = _INTERNAL_ASSISTANT_PREFIX_RE.sub('', s).lstrip()
    s = _INTERNAL_ACTION_PREFIX_RE.sub('', s).lstrip()
    if not s.strip():
        return ''
    # Drop internal title-generation tasks and strip tool-usage hints that may
    # be injected by upstream adapters before the real human message. These are
    # not user-visible clawbot dialogue and must never persist as char:main
    # chat history.
    if _INTERNAL_TITLE_TASK_RE.search(s):
        return ''
    s = _INTERNAL_VISION_HINT_RE.sub('', s).lstrip()
    s = _strip_client_file_hint_prefix(s)
    if _INTERNAL_TITLE_TASK_RE.search(s):
        return ''
    # Some persisted user turns are stringified multimodal text payloads whose
    # text begins with injected SYSTEM/time/output-format/frontend-entry/fact
    # blocks followed by the real human message.  Drop those prompt shells as
    # whole lines/blocks and keep only the trailing dialogue.  Use line-based
    # removal for SYSTEM because the frontend-entry line can contain nested
    # Chinese brackets like 【.微信平台跨次元对话.】, which makes a simple [^】]*
    # pattern stop too early and leak the rest of the instruction.
    lines = s.splitlines()
    while lines:
        first = lines[0].lstrip()
        if not first.startswith('【SYSTEM'):
            break
        # Single-line SYSTEM shell, including frontend-entry lines that may
        # contain nested Chinese brackets such as 【.微信平台跨次元对话.】.
        if first.rstrip().endswith('】'):
            lines.pop(0)
            continue
        # Multi-line SYSTEM shell, e.g. the selected fact-memory block:
        #   【SYSTEM ...：
        #   - [fact] ...
        #   】
        lines.pop(0)
        while lines:
            line = lines.pop(0)
            if line.strip() == '】':
                break
    s = '\n'.join(lines).lstrip()
    # Strip all remaining internal trigger blocks including mid-text ones like
    # 【同期群聊原文】, 【可选日常来源】, 【私下意图】, 【上下文】, 【话题】 that
    # were not caught by _strip_clawbot_runtime_wrapper (line-start only).
    # Marker-line only: do not match bare 上下文 / 话题 in normal dialogue.
    _prev = None
    while _prev != s:
        _prev = s
        s = re.sub(
            r'(?is)【(?:随机自主唤醒|私聊主动发起[^】]*|可选日常来源|私下意图|同期群聊原文|上下文|话题|SYSTEM\s*最近\d+分钟微信群聊现场)[^】]*】[^\n]*(?:\n|$)',
            '',
            s
        ).lstrip()
    s = re.sub(r'(?im)^\s*【\.[^\n】]+\.】[^\n]*(?:\n|$)', '', s)
    s = _prefer_dialog_marked_tail(s)
    m = _DIALOG_MARK_RE.search(s)
    if m:
        s = m.group(1).lstrip('：: \t')
    s = _LEAK_BLOCK_RE.sub('', s)
    s = _strip_task_done_trailing_note(s)
    s = _strip_leading_markdown_thought_sections(s)
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
    parts = [p.strip() for p in re.split(r'\n\s*\n', s) if p.strip()]
    if len(parts) >= 2 and len(parts) % 2 == 0:
        mid = len(parts) // 2
        def _norm_dupe_block(blocks):
            joined = '\n'.join(blocks)
            joined = re.sub(r'\s+', '', joined)
            joined = re.sub(r'[。.!！?？…]+$', '', joined)
            return joined
        if _norm_dupe_block(parts[:mid]) == _norm_dupe_block(parts[mid:]):
            s = '\n\n'.join(parts[:mid])
    return s





_API_CONTEXT_MARK_RE = re.compile(r'^\s*(【\.[^\n】]+\.】)\s*(?:\n|$)')
# Home/API frontend may wrap real user text in a model/date shell, e.g.
#   【.家园中互动.】
#   : Current model: xinghui-gemini-3-pro
#   Current date: 2026-07-23
#    Additional info for this conversation:
#   [user]: （吻了上去）
_HOME_MODEL_SHELL_USER_RE = re.compile(
    r'(?is)(?:^|\n)\s*\[user\]\s*:\s*(.*)\s*$'
)
_HOME_MODEL_SHELL_NOISE_RE = re.compile(
    r'(?is)^\s*:?\s*Current model\s*:.*$'
    r'|^\s*Current date\s*:.*$'
    r'|^\s*Additional info for this conversation\s*:?\s*$'
)
_INTERNAL_LONG_PROMPT_PLACEHOLDER_RE = re.compile(
    r'(?is)^\s*Long user prompt saved to\s+\S*[/\\]user_prompt_\d+_\d+\.md\.\s*Read and execute\.\s*$'
)


def _is_internal_long_prompt_placeholder(text) -> bool:
    """True for GA's internal long-prompt file handoff message.

    agentmain.put_task writes oversized prompts to temp/user_prompt_*.md and
    appends a short instruction to native backend.history.  That instruction is
    an implementation detail, not a visible user turn; persisting it into the
    shared frontend history makes later API/home turns look one step behind.
    """
    raw = str(_maybe_parse_text_payload(text) or '')
    return bool(_INTERNAL_LONG_PROMPT_PLACEHOLDER_RE.match(raw.strip()))


def _extract_home_model_shell_user_body(text: str) -> str | None:
    """If text is a home/API model-shell wrapper, return the real [user] body."""
    raw = str(text or '')
    if 'Current model' not in raw and 'Additional info for this conversation' not in raw:
        return None
    m = _HOME_MODEL_SHELL_USER_RE.search(raw)
    if not m:
        return None
    body = (m.group(1) or '').strip()
    return body if body else ''


def _visible_user_dialog_only(text):
    """Sanitize persisted user turns while preserving API frontend context marks.

    API/home/chat frontends use a leading marker such as ``【.家园互动.】`` to
    label where a user turn came from.  The general visible-dialog cleaner must
    strip these markers before sending text to WeChat, but backend_history needs
    to keep the leading marker on *user* turns so later context knows the source.
    """
    raw = str(_maybe_parse_text_payload(text) or '')
    m = _API_CONTEXT_MARK_RE.match(raw)
    mark = m.group(1) if m else ''
    body = raw[m.end():] if m else raw
    # Prefer real dialogue after home/API model-shell wrappers.
    shell_body = _extract_home_model_shell_user_body(body if mark else raw)
    if shell_body is not None:
        clean = _visible_dialog_only(shell_body)
        clean = str(clean or '').strip()
        if mark:
            return f'{mark}\n{clean}' if clean else mark
        return clean
    if not m:
        return _visible_dialog_only(raw)
    clean = _visible_dialog_only(body)
    clean = str(clean or '').strip()
    # Drop residual model-shell noise lines if shell extractor missed [user].
    if clean and ('Current model' in clean or 'Additional info for this conversation' in clean):
        kept = []
        for line in clean.splitlines():
            if _HOME_MODEL_SHELL_NOISE_RE.match(line.strip()):
                continue
            if re.match(r'(?is)^\s*\[user\]\s*:\s*', line):
                line = re.sub(r'(?is)^\s*\[user\]\s*:\s*', '', line)
            kept.append(line)
        clean = '\n'.join(kept).strip()
    return f'{mark}\n{clean}' if clean else mark


def _content_to_text_blocks(content):
    """Return GA native text-block content for backend.history."""
    text = _extract_text_payload(content)
    if not isinstance(text, str):
        text = str(text or '')
    text = text.strip()
    return [{'type': 'text', 'text': text}] if text else []


def _sanitize_backend_history(hist):
    """Keep persistent frontend backend history to visible dialogue only.

    Native GA sessions expect message content to be a list of text blocks.  The
    OpenAI-compatible API and clawbot both persist through this function so a
    fixed session key (for example ``char:main``) has one canonical on-disk
    format and does not keep tool/thinking/summary/frontend wrapper debris.
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
            content = _maybe_parse_text_payload(content)
            if role == 'user' and _is_internal_long_prompt_placeholder(content):
                continue
            if _is_internal_title_task(str(content or '')):
                skip_next_title_reply = True
                continue
            content = _visible_user_dialog_only(content) if role == 'user' else _visible_dialog_only(content)
            if not str(content or '').strip():
                continue
            skip_next_title_reply = False
            new_item['content'] = _content_to_text_blocks(content)
        elif isinstance(content, list):
            content = _extract_text_payload(content)
            if role == 'user' and _is_internal_long_prompt_placeholder(content):
                continue
            if _is_internal_title_task(str(content or '')):
                skip_next_title_reply = True
                continue
            content = _visible_user_dialog_only(content) if role == 'user' else _visible_dialog_only(content)
            if not str(content or '').strip():
                continue
            skip_next_title_reply = False
            new_item['content'] = _content_to_text_blocks(content)
        else:
            continue
        cleaned.append(new_item)
    return cleaned

def get_backend_history(agent) -> list:
    try:
        hist = agent.llmclient.backend.history
        return hist if isinstance(hist, list) else []
    except Exception:
        return []


def set_backend_history(agent, hist) -> bool:
    if not isinstance(hist, list):
        return False
    try:
        agent.llmclient.backend.history = hist
        return True
    except Exception as e:
        print(f'[session-history] set backend history failed: {type(e).__name__}: {e}', file=sys.__stdout__)
        return False


def load_session_state(path: Path | None = None) -> dict:
    state_path = Path(path or DEFAULT_SESSION_STATE_PATH)
    try:
        if state_path.exists():
            data = json.loads(state_path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f'[session-history] load state failed: {type(e).__name__}: {e}', file=sys.__stdout__)
    return {}


def save_session_state(data: dict, path: Path | None = None) -> None:
    state_path = Path(path or DEFAULT_SESSION_STATE_PATH)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(state_path.suffix + '.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        tmp.replace(state_path)
    except Exception as e:
        print(f'[session-history] save state failed: {type(e).__name__}: {e}', file=sys.__stdout__)


def load_backend_history(session_key: str, path: Path | None = None, *, sanitize_and_write: bool = True) -> list:
    with _SESSION_LOCK:
        data = load_session_state(path)
        entry = data.get(session_key) if isinstance(data, dict) else None
        hist = entry.get('backend_history') if isinstance(entry, dict) else None
        if not isinstance(hist, list):
            return []
        clean = _sanitize_backend_history(hist)
        if sanitize_and_write and clean != hist:
            if not isinstance(entry, dict):
                entry = {}
            entry['backend_history'] = clean
            entry.setdefault('agent_history', [])
            data[session_key] = entry
            save_session_state(data, path)
            print(f'[session-history] sanitized backend_history key={session_key} n={len(clean)}', file=sys.__stdout__)
        return clean


def _msg_text(msg) -> str:
    """Flatten a backend history item to plain text for archive/summary."""
    if not isinstance(msg, dict):
        return str(msg or '')
    content = msg.get('content', '')
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get('type', 'text') in ('text', ''):
                parts.append(str(block.get('text', '') or ''))
            elif isinstance(block, str):
                parts.append(block)
        return '\n'.join(p for p in parts if p)
    return str(content or '')


def _archive_history_to_l4(session_key: str, items: list) -> Path | None:
    """Append dropped full-detail turns to L4 all_histories + raw dump. Never lose content.

    Platform/timeline cues (【.xxx.】) are preserved both in the text dump and in
    structured JSON so far-history mining can still tell WeChat / home / API apart.
    """
    if not items:
        return None
    try:
        _L4_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_key = re.sub(r'[^\w.:-]+', '_', session_key)
        session_name = f'{safe_key}_compress_{stamp}'
        lines = []
        sources: list[str] = []
        for msg in items:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get('role') or 'unknown')
            tag = '[USER]' if role == 'user' else ('[Agent]' if role == 'assistant' else f'[{role}]')
            body = _msg_text(msg).strip()
            if not body:
                continue
            src = ''
            m = re.search(r'【\.([^【】\r\n]{1,80}?)\.】', body)
            if m:
                src = m.group(1).strip()
                if src and src not in sources:
                    sources.append(src)
            # Keep multi-line body; first line tagged with optional platform cue.
            body_lines = body.splitlines() or ['']
            src_tag = f'[{src}]' if src else ''
            lines.append(f'{tag}{src_tag} {body_lines[0]}')
            for extra in body_lines[1:]:
                lines.append(extra)
        if not lines:
            return None
        sep = '=' * 60
        src_line = f"SOURCES: {', '.join(sources) if sources else '(unlabeled)'}\n"
        block = (
            f"\n{sep}\nSESSION: {session_name}\nKEY: {session_key}\n{src_line}{sep}\n"
            + '\n'.join(lines)
            + '\n'
        )
        hist_path = _L4_DIR / 'all_histories.txt'
        with open(hist_path, 'a', encoding='utf-8') as f:
            f.write(block)
        # Also dump structured JSON for recovery / later mining.
        raw_path = _L4_DIR / f'backend_history_archive_{safe_key}_{stamp}.json'
        raw_path.write_text(
            json.dumps(
                {
                    'session_key': session_key,
                    'archived_at': datetime.now().isoformat(timespec='seconds'),
                    'n_items': len(items),
                    'sources': sources,
                    'items': items,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding='utf-8',
        )
        print(
            f'[session-history] L4 archived key={session_key} n={len(items)} '
            f'sources={sources or ["(unlabeled)"]} session={session_name} raw={raw_path.name}',
            file=sys.__stdout__,
        )
        return raw_path
    except Exception as e:
        print(f'[session-history] L4 archive failed key={session_key}: {type(e).__name__}: {e}', file=sys.__stdout__)
        return None


def _summary_user_body(text: str) -> str:
    """Extract a short, noise-free user body for compress summary snippets."""
    raw = str(text or '').strip()
    if not raw:
        return ''
    # Prefer sanitize path so long-prompt refs / model shells never enter summary.
    # Note: _visible_user_dialog_only can empty same-line 【.平台.】 dialog (mark
    # regex requires newline). Always keep a raw-strip fallback for summary.
    cleaned = ''
    try:
        cleaned = str(_visible_user_dialog_only(raw) or '').strip()
    except Exception:
        cleaned = ''
    if not cleaned:
        cleaned = str(_maybe_parse_text_payload(raw) or raw).strip()
    # Strip leading source marker (same-line OR newline form) / time / speaker.
    body = re.sub(r'^【\.[^【】\r\n]+\.】\s*', '', cleaned)
    body = re.sub(r'^\[[^\]]+\]\s*', '', body)
    body = re.sub(r'^[^:：\n]{1,20}[:：]\s*', '', body, count=1)
    body = re.sub(r'\s+', ' ', body).strip()
    if not body:
        return ''
    low = body.lower()
    noise_keys = (
        'long user prompt saved to',
        'current model:',
        'additional info for this conversation',
        'read and execute',
        'user_prompt_',
        'if you need to show files to user',
        '[file:filepath]',
        'working memory',
        'system tips',
        '有不清晰的地方请再次读取',
        '必须在回复文本中包含',
        '<summary>',
        '</summary>',
        'ga_shared_history',
        'backend_history',
        'final_answer',
    )
    if any(k in low for k in noise_keys):
        return ''
    # Drop pure RP stage-direction / OOC control lines (not spoken memory).
    # Note: home RP often uses FULLWIDTH braces ｛｝ and parens （）.
    pure = body.strip()
    m_wrap = re.fullmatch(
        r'[\(（\{｛［\[【](.{0,80}?)[\)）\}｝］\]】]',
        pure,
    )
    if m_wrap:
        inner = m_wrap.group(1).strip()
        # OOC / director tags — never enter char memory summary.
        if any(
            k in inner
            for k in (
                '前戏', '扩张', '引导', '可代替', '高潮', '挑逗', '剧情',
                '系统', 'ooc', 'OOC', '温柔', '激烈',
            )
        ):
            return ''
        # Bare short action beat with no spoken words: skip for far-history summary
        # (full beat stays in L4; summary needs conversational substance).
        if len(inner) <= 16 and not re.search(r'[，。！？、,.!?…]', inner):
            return ''
        body = inner
        pure = body
        low = body.lower()
    # Whole-body pure stage / OOC still after unwrap attempt.
    if re.fullmatch(r'[\(（\{｛［\[【].{0,80}[\)）\}｝］\]】]', body):
        return ''
    if any(k in body for k in ('可代替user', '前戏，', '扩张，')):
        return ''
    # Drop pure tool/path dumps.
    if re.search(r'(?i)/home/\S+\.(py|md|json|txt)\b', body) and len(body) < 120:
        if any(k in low for k in ('saved to', 'read ', 'file_read', 'path')):
            return ''
    # Soft-drop noisy group-chat @ pings that aren't private dialog.
    if '@_user_' in body and len(body) < 40:
        return ''
    return body


def _summary_assistant_body(text: str) -> str:
    """Visible assistant snippet for compress; prefer spoken lines over stage notes."""
    raw = str(text or '').strip()
    if not raw:
        return ''
    # Prefer FINAL_ANSWER payload when present (clawbot/API wrappers).
    try:
        fa = _extract_final_answer(raw)
        if fa:
            raw = fa
    except Exception:
        pass
    try:
        cleaned = str(_visible_dialog_only(raw) or '').strip()
    except Exception:
        cleaned = raw
    if not cleaned:
        return ''
    # Strip residual answer/markup wrappers that leak into shared summary.
    cleaned = re.sub(r'【/?FINAL_ANSWER】', ' ', cleaned)
    cleaned = re.sub(r'(?is)</?final_answer>', ' ', cleaned)
    # Prefer dialogue outside full-width stage directions if mixed.
    spoken = re.sub(r'（[^）]{0,200}）', ' ', cleaned)
    spoken = re.sub(r'\[[^\]]{0,200}\]', ' ', spoken)
    spoken = re.sub(r'\{[^\}]{0,80}\}', ' ', spoken)
    spoken = re.sub(r'｛[^｝]{0,80}｝', ' ', spoken)
    spoken = re.sub(r'^[\s\]】>]+', '', spoken)
    spoken = re.sub(r'\s+', ' ', spoken).strip()
    body = spoken or re.sub(r'\s+', ' ', cleaned).strip()
    body = re.sub(r'^[\s\]】>]+', '', body).strip()
    # Reject residual director tags if they leaked as whole body.
    if re.fullmatch(r'[\(（\{｛［\[【].{0,80}[\)）\}｝］\]】]', body):
        return ''
    if any(k in body for k in ('可代替user', '前戏，', '扩张，')):
        return ''
    low = body.lower()
    if any(
        k in low
        for k in (
            'tool call',
            'file_read',
            'working memory',
            'long user prompt',
            'current model:',
            'summary_len',
            'backend_history',
            'final_answer',
        )
    ):
        return ''
    return body


def _sample_timeline_indices(n: int, max_points: int = 16) -> list:
    """Evenly sample message indices across far history for timeline coverage."""
    if n <= 0:
        return []
    if n <= max_points:
        return list(range(n))
    # Always include head/tail; fill mid points evenly.
    pts = {0, n - 1}
    for i in range(1, max_points - 1):
        pts.add(int(round(i * (n - 1) / (max_points - 1))))
    return sorted(pts)


def _build_extractive_summary(items: list, session_key: str) -> dict:
    """Char-perspective extractive summary of far history (no extra LLM call).

    Written as 沈星回 first-person memory notes with platform/timeline cues so
    shared xinghui:main stays coherent across WeChat / home / API.
    Full detail remains in L4_raw_sessions.
    """
    sources = []
    events = []  # (idx, kind, src, body)
    n = len(items) if isinstance(items, list) else 0
    for idx, msg in enumerate(items or []):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get('role') or '')
        text = _msg_text(msg).strip()
        if not text:
            continue
        # Nested prior compress marker: keep only its bullet lines as memory seed.
        if text.lstrip().startswith(_COMPRESS_MARKER):
            for line in text.splitlines():
                s = line.strip()
                if s.startswith('- '):
                    body = s[2:].strip()
                    # Normalize third-person bullets into first-person seed later.
                    if body and body not in (e[3] for e in events[-8:]):
                        events.append((idx, 'note', '', body))
            continue
        src = ''
        m = re.search(r'【\.([^【】\r\n]{1,80}?)\.】', text)
        if m:
            src = m.group(1).strip()
            if src and src not in sources:
                sources.append(src)
        if role == 'user':
            body = _summary_user_body(text)
            if body:
                events.append((idx, 'user', src, body))
        elif role == 'assistant':
            body = _summary_assistant_body(text)
            if body:
                events.append((idx, 'assistant', src, body))

    # Timeline sample: prefer evenly spaced user beats, pair nearby assistant reply.
    user_events = [e for e in events if e[1] == 'user']
    note_events = [e for e in events if e[1] == 'note']
    picked = []
    seen_bodies = set()

    def _add_pair(u_ev):
        idx, _, src, body = u_ev
        key = body[:48]
        if key in seen_bodies:
            return
        seen_bodies.add(key)
        src_tag = f'[{src}]' if src else ''
        # Character first-person memory line.
        u_snip = body if len(body) <= 72 else body[:69] + '…'
        line = f'言言{src_tag}说过：{u_snip}'
        # Nearest later assistant reply within a few messages.
        a_body = ''
        for j in range(idx + 1, min(idx + 4, n)):
            msg = items[j] if j < n else None
            if not isinstance(msg, dict) or str(msg.get('role') or '') != 'assistant':
                continue
            a_body = _summary_assistant_body(_msg_text(msg))
            if a_body:
                break
        if a_body:
            a_snip = a_body if len(a_body) <= 48 else a_body[:45] + '…'
            line = f'{line}；我回应：{a_snip}'
        picked.append(line)

    # Always keep early / mid / late user beats for timeline.
    if user_events:
        sample_n = min(12, len(user_events))
        if len(user_events) <= sample_n:
            chosen = user_events
        else:
            idxs = _sample_timeline_indices(len(user_events), sample_n)
            chosen = [user_events[i] for i in idxs]
        for ev in chosen:
            _add_pair(ev)
            if len(picked) >= 12:
                break
    # Seed from prior compress notes if thin.
    if len(picked) < 6:
        for ev in note_events:
            body = ev[3]
            # Drop noisy legacy bullets.
            low = body.lower()
            if any(k in low for k in ('long user prompt', 'current model', 'user_prompt_', 'summary_len')):
                continue
            # Convert legacy speaker-name bullets into first-person if present.
            if body.startswith('言言：'):
                line = f'言言说过：{body[3:]}'
            elif body.startswith('沈星回：'):
                line = f'我记得自己说过：{body[3:]}'
            else:
                line = body
            key = line[:48]
            if key in seen_bodies:
                continue
            seen_bodies.add(key)
            picked.append(line if len(line) <= 120 else line[:117] + '…')
            if len(picked) >= 10:
                break

    src_txt = '、'.join(sources[:8]) if sources else '多前端'
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    head = (
        f'{_COMPRESS_MARKER}｜{session_key}｜至 {stamp}｜来源:{src_txt}｜'
        f'已归档远史 {len(items)} 条至 L4】\n'
        '（以下是我以沈星回视角整理的远史要点；近20轮完整原文仍保留在当前上下文，'
        '更早完整对话在 memory/L4_raw_sessions）\n'
    )
    body = '\n'.join(f'- {s}' for s in picked[:14]) or '- （这段远史没有可抽取的有效对话）'
    summary_text = head + body
    return {
        'role': 'user',
        'content': [{'type': 'text', 'text': summary_text}],
    }


def _maybe_compress_backend_history(session_key: str, hist: list) -> tuple[list, bool]:
    """If hist exceeds soft limit, archive older half to L4 and keep summary + recent.

    Returns (new_hist, did_compress).
    """
    if not isinstance(hist, list) or len(hist) < max(_HISTORY_SOFT_LIMIT, _HISTORY_MIN_FOR_COMPRESS):
        return hist, False
    # Keep the newest HARD_KEEP items; compress everything before that.
    keep_n = min(_HISTORY_HARD_KEEP, len(hist) - 10)
    if keep_n < 20:
        return hist, False
    drop_n = len(hist) - keep_n
    if drop_n < 15:
        return hist, False
    older = hist[:drop_n]
    recent = hist[drop_n:]
    # Don't re-compress an already-leading summary alone; merge older including prior summary.
    _archive_history_to_l4(session_key, older)
    summary_msg = _build_extractive_summary(older, session_key)
    # Drop any previous leading compress marker from recent to avoid stacked walls of text.
    trimmed_recent = []
    for msg in recent:
        t = _msg_text(msg)
        if t.lstrip().startswith(_COMPRESS_MARKER):
            continue
        trimmed_recent.append(msg)
    new_hist = [summary_msg] + trimmed_recent
    print(
        f'[session-history] compressed key={session_key} '
        f'from={len(hist)} drop={drop_n} keep={len(trimmed_recent)} -> {len(new_hist)}',
        file=sys.__stdout__,
    )
    return new_hist, True


def save_backend_history(session_key: str, hist: list, path: Path | None = None, *, return_history: bool = False):
    with _SESSION_LOCK:
        clean = _sanitize_backend_history(hist)
        # Intentional shared-session budget: summarize + L4-archive far history.
        clean, did_compress = _maybe_compress_backend_history(session_key, clean)
        data = load_session_state(path)
        entry = data.get(session_key)
        if not isinstance(entry, dict):
            entry = {}
        # Guard: never let an empty / catastrophically short in-memory hist wipe disk.
        # Empty or partial agent backends (proactive/home-note/API race) were overwriting xinghui:main.
        # Intentional compress (did_compress) is allowed to shrink past the half-size guard.
        existing = entry.get('backend_history') if isinstance(entry.get('backend_history'), list) else []
        if existing:
            if not clean:
                print(
                    f'[session-history] refuse empty overwrite key={session_key} keep_n={len(existing)}',
                    file=sys.__stdout__,
                )
                return (len(existing), existing) if return_history else len(existing)
            # Refuse shrink to less than half when disk already has a real conversation,
            # unless this save itself performed intentional compress+L4 archive.
            if (
                not did_compress
                and len(existing) >= 10
                and len(clean) < max(5, len(existing) // 2)
            ):
                print(
                    f'[session-history] refuse shrink overwrite key={session_key} '
                    f'keep_n={len(existing)} got_n={len(clean)}',
                    file=sys.__stdout__,
                )
                return (len(existing), existing) if return_history else len(existing)
        entry['backend_history'] = clean
        entry.setdefault('agent_history', [])
        if did_compress:
            entry['last_compress_at'] = datetime.now().isoformat(timespec='seconds')
            entry['last_compress_n'] = len(clean)
        data[session_key] = entry
        save_session_state(data, path)
        print(f'[session-history] persisted backend_history key={session_key} n={len(clean)}', file=sys.__stdout__)
        return (len(clean), clean) if return_history else len(clean)


def restore_backend_history(agent, session_key: str, path: Path | None = None, *, force: bool = False) -> int:
    with _SESSION_LOCK:
        current = get_backend_history(agent)
        if current and not force:
            return len(current)
        hist = load_backend_history(session_key, path)
        if hist:
            set_backend_history(agent, hist)
            print(f'[session-history] restored backend_history key={session_key} n={len(hist)}', file=sys.__stdout__)
            return len(hist)
        return len(current)


def persist_agent_backend_history(agent, session_key: str, path: Path | None = None) -> int:
    n, clean = save_backend_history(session_key, get_backend_history(agent), path, return_history=True)
    if isinstance(clean, list):
        set_backend_history(agent, clean)
    return n

# Backward-compatible exports for callers that need the sanitizer/final text gate.
sanitize_backend_history = _sanitize_backend_history
visible_dialog_only = _visible_dialog_only
content_to_text_blocks = _content_to_text_blocks
extract_text_payload = _extract_text_payload
