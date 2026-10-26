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
    match = _CJK_RE.search(text)
    if not match:
        return text.strip()
    prefix = text[:match.start()]
    if prefix and re.search(r"[A-Za-z]", prefix) and not _CJK_RE.search(prefix):
        return text[match.start():].lstrip(" `：:，,。.!！?？\n\t")
    return text.strip()


def _visible_text(raw: str) -> str:
    text = str(raw or "")
    match = _FINAL_MARK_RE.search(text)
    if match:
        text = match.group(1)
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
    prompt = storage.prompt_for(slug, session_id)
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
        with _locks[key]:
            q = agent.put_task(text, source=key)
            raw_result = ""
            visible_result = ""
            while True:
                try:
                    item = q.get(timeout=1800)
                except queue.Empty as exc:
                    raise TimeoutError("roleplay generation timeout") from exc
                if "next" in item:
                    raw_result += str(item.get("next", ""))
                    continue
                if "done" in item:
                    raw_result = str(item.get("done", "")) or raw_result
                    visible_result = _visible_text(raw_result)
                    if visible_result:
                        yield visible_result
                    break
                if "error" in item:
                    raise RuntimeError(f"roleplay backend error: {item['error']}")

            persist_agent_backend_history(agent, key, storage.HISTORY)
            storage.record_turn(slug, session_id, text, visible_result)
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
