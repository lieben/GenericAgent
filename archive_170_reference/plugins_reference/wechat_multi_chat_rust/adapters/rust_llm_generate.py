#!/usr/bin/env python3
"""LLM generation adapter for Rust WeChat multi-chat.

stdin:  {agent,target,prompt,session_key}
stdout: {reply, source, session_key}

No clowbot/wechatapp dependency.  Each target gets its own Agent source label so
message streams and debug output remain attributable per group/private chat.
"""
import json
import queue
import re
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'plugins' / 'local_frontends'))
try:
    from prompt_config import get_prompt_config
except Exception:
    def get_prompt_config(_path, default=None):
        return default


# --- GA-internal debug markers that should never leak into a chat reply ---
# GA prints `Turn N ...` separators and `<summary>...</summary>` reasoning blocks
# to its verbose stream. When the upstream LLM errors (e.g. HTTP 502) the agent
# may also yield `!!!Error: ...` / `Error: HTTP ...` / `all channels failed`
# strings as the "final" answer. None of these belong in a WeChat group.
_TURN_HEAD_RE = re.compile(r'^\s*Turn\s+\d+\s*\.{0,3}\s*$', re.IGNORECASE)
_SUMMARY_BLOCK_RE = re.compile(r'<summary>.*?</summary>', re.IGNORECASE | re.DOTALL)
_ERROR_LINE_RE = re.compile(
    r'^\s*(?:!!!?\s*)?(?:Error|HTTP\s+\d+|all\s+channels\s+failed)\b.*$',
    re.IGNORECASE,
)
_DECISION_HEAD_RE = re.compile(
    r'^\s*DECISION\s*[:\s]\s*(?:REPLY|SKIP)\b.*$',
    re.IGNORECASE,
)


def clean_reply(text: str) -> str:
    text = (text or '').strip()
    if not text:
        return ''

    # 1. drop markdown fences around the whole reply
    if text.startswith('```') and text.endswith('```'):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = '\n'.join(lines[1:-1]).strip()

    # 2. strip <summary>...</summary> blocks anywhere in the body
    text = _SUMMARY_BLOCK_RE.sub('', text)

    # 3. strip GA-internal leading noise + error lines globally
    cleaned_lines = []
    for line in text.splitlines():
        if _TURN_HEAD_RE.match(line):
            continue
        if _ERROR_LINE_RE.match(line):
            continue
        cleaned_lines.append(line)
    text = '\n'.join(cleaned_lines).strip()

    # 4. strip a stray first-line DECISION header (Rust also strips it, but we
    #    belt-and-brace so downstream logs never see it either)
    first_nl = text.find('\n')
    if first_nl == -1:
        if _DECISION_HEAD_RE.match(text):
            text = ''
    else:
        first_line, rest = text[:first_nl], text[first_nl + 1:]
        if _DECISION_HEAD_RE.match(first_line):
            text = rest.lstrip('\n').strip()

    # 5. polite-label prefixes, same as before
    for prefix in tuple(get_prompt_config('common.reply_prefixes_to_strip', ['回复：', '回复:', '正文：', '正文:'])):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()

    return text.strip()


def main():
    req = json.load(sys.stdin)
    prompt = req.get('prompt') or ''
    agent_cfg = req.get('agent') or {}
    target = req.get('target') or {}
    target_id = target.get('id') or 'unknown'
    session_key = req.get('session_key') or target.get('session_key') or agent_cfg.get('session_key') or f'wechat_multi:{target_id}'
    source = f'wechat_multi:{target_id}'
    try:
        from agentmain import GeneraticAgent
        agent = GeneraticAgent()
        agent.llm_no = int(agent_cfg.get('llm_no') or 0)
        agent.next_llm(agent.llm_no)
        try:
            agent.llmclient.backend.stream = False
        except Exception:
            pass
        agent.verbose = False
        agent.inc_out = False
        agent.task_dir = str(ROOT / 'temp')
        out_q = queue.Queue()
        worker = threading.Thread(target=agent.run, daemon=True)
        worker.start()
        wrapped = (
            f"[微信多聊独立系统 | session={session_key} | target={target_id} | kind={target.get('kind')} | title={target.get('wechat_title')}]\n"
            f"{prompt}"
        )
        agent.task_queue.put({'query': wrapped, 'source': source, 'output': out_q})
        final = ''
        timeout_s = int(agent_cfg.get('generation_timeout_s') or 90)
        while True:
            item = out_q.get(timeout=timeout_s)
            if 'done' in item:
                final = item.get('done') or ''
                break
        try:
            agent.abort()
        except Exception:
            pass
        print(json.dumps({'reply': clean_reply(final), 'source': source, 'session_key': session_key}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({'reply': '', 'adapter_error': repr(e), 'target': target_id, 'source': source, 'session_key': session_key}, ensure_ascii=False))

if __name__ == '__main__':
    main()
