#!/usr/bin/env python3
"""Flash decision adapter for Rust WeChat multi-chat.

stdin:  {agent,target,messages,state,gate}
stdout: {used,should_reply,reason,trigger_msg_id,trigger_sender,...}

Contract:
- Rust owns orchestration, target state, cursor progression, and trigger discovery.
- This adapter only decides SEND/SKIP for fresh peer messages when Rust asks it to judge.
- If flash_decision.model is configured, try the GeneraticAgent small model and
  parse only <decision>SEND</decision> / <decision>SKIP</decision>.
- On missing config, timeout, parse error, or GA failure, return a visible fallback
  result for offline safety; production acceptance requires real model SEND/SKIP evidence.
"""
import json
import os
import re
import subprocess
import sys
import time
import signal
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[3]
LOCAL_FRONTENDS = ROOT / 'plugins' / 'local_frontends'
if str(LOCAL_FRONTENDS) not in sys.path:
    sys.path.insert(0, str(LOCAL_FRONTENDS))
try:
    from prompt_config import format_prompt
except Exception:
    def format_prompt(_key: str, default: str = '', **values: Any) -> str:
        out = default
        for k, v in values.items():
            out = out.replace('{{' + k + '}}', str(v))
        return out
TASK_DIR_NAME = os.environ.get('WECHAT_MULTI_FLASH_TASK_DIR', 'wechat_multi_flash_decide')
TASK_DIR = ROOT / 'temp' / TASK_DIR_NAME

# 隔离 stdout：GA/agentmain import 会往 stdout 打启动日志（[MultiAgent]/[Info] Load mykeys...），
# 会污染 Rust 解析的 JSON。我们把进程的 stdout 重定向到 stderr，
# 只保留一个原始 stdout 的副本 _REAL_STDOUT_FD 用于最后输出决策 JSON。
try:
    _REAL_STDOUT_FD = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = os.fdopen(2, 'w', encoding='utf-8', closefd=False)
except Exception:
    _REAL_STDOUT_FD = None


def emit_result(payload: Dict[str, Any]) -> None:
    """把最终决策 JSON 写入真正的 stdout（绕过被重定向的 sys.stdout）。"""
    data = json.dumps(payload, ensure_ascii=False) + '\n'
    if _REAL_STDOUT_FD is not None:
        try:
            os.write(_REAL_STDOUT_FD, data.encode('utf-8'))
            return
        except Exception:
            pass
    # 兜底：如果真 stdout 不可用，仍写到当前 stdout
    sys.__stdout__.write(data)
    sys.__stdout__.flush()


def latest_peer(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for m in reversed(messages or []):
        if not m.get('is_self') and (m.get('text') or '').strip():
            return m
    return None


def extract_visible(raw: str) -> str:
    text = raw or ''
    text = re.sub(r'<summary>.*?</summary>', '', text, flags=re.S | re.I)
    text = re.sub(r'\[ROUND END\]\s*$', '', text).strip()
    return text


def parse_decision(raw: str) -> str:
    visible = extract_visible(raw)
    err = re.search(
        r'!!!Error|ReadTimeout|HTTP\s*50\d|Bad Gateway|circuit breaker|all channels failed|upstream error',
        visible,
        re.I,
    )
    if err:
        return 'ERROR'
    m = re.search(r'<decision>\s*(SEND|SKIP)\s*</decision>', visible, re.I)
    if m:
        return m.group(1).upper()
    lines = [x.strip() for x in visible.splitlines() if x.strip()]
    first = lines[0].upper() if lines else ''
    if first in {'SEND', 'SKIP'}:
        return first
    return 'ERROR'


def run_llm_direct(prompt: str, llm_no: int) -> str:
    """Call configured GA backend directly without agent_runner/tools/session history.

    Flash decision is a tiny SEND/SKIP classification.  It must not go through
    agentmain --task because that path mounts full Agent prompt/tools/working
    memory and can spend tens of seconds before the small model even answers.
    It must also avoid backend.ask() on stateful GA sessions, because ask()
    appends to backend.history and makes repeated flash decisions carry an
    ever-growing context.  raw_ask([single_user_msg]) is intentionally used as
    an isolated one-shot LLM call.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from agentmain import GeneraticAgent

    agent = GeneraticAgent()
    client = agent.llmclients[int(llm_no) % len(agent.llmclients)]
    backend = client.backend

    msg = {'role': 'user', 'content': [{'type': 'text', 'text': prompt}]}

    # Avoid polluting any persistent/shared history: use the low-level backend
    # with one isolated user message. Native/raw-capable backends stream chunks
    # from raw_ask(messages).  Do not call backend.ask(msg) here: ask() is the
    # stateful session wrapper and appends to backend.history.
    if hasattr(backend, 'raw_ask'):
        try:
            gen = backend.raw_ask([msg])
            chunks = []
            try:
                while True:
                    chunks.append(next(gen))
            except StopIteration as e:
                content_blocks = e.value or []
                for block in content_blocks:
                    if isinstance(block, dict) and block.get('type') == 'text':
                        chunks.append(block.get('text', ''))
            return ''.join(str(x) for x in chunks)
        finally:
            try:
                backend.history = []
            except Exception:
                pass

    # Legacy/simple backends may only expose ask(str).  Since those objects are
    # local to this short-lived adapter process, clear any accidental history
    # before and after the call.
    try:
        backend.history = []
    except Exception:
        pass
    gen = backend.ask(prompt)
    chunks = []
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration as e:
        if e.value is not None:
            chunks.append(str(e.value))
    finally:
        try:
            backend.history = []
        except Exception:
            pass
    return ''.join(str(x) for x in chunks)


def run_ga_task(prompt: str, timeout: int, llm_no: int) -> str:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    for old in TASK_DIR.glob('output*.txt'):
        try:
            old.unlink()
        except Exception:
            pass
    (TASK_DIR / 'input.txt').write_text(prompt, encoding='utf-8')
    cmd = [
        sys.executable,
        str(ROOT / 'agentmain.py'),
        '--task',
        TASK_DIR_NAME,
        '--llm_no',
        str(llm_no),
    ]
    p = subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    outputs = sorted(TASK_DIR.glob('output*.txt'), key=lambda x: x.stat().st_mtime)
    body = outputs[-1].read_text(encoding='utf-8', errors='ignore') if outputs else ''
    if p.returncode != 0:
        raise RuntimeError(f'agentmain rc={p.returncode} stderr={p.stderr[-1200:]} body={body[-1200:]}')
    return body or p.stdout


def resolve_llm_no_by_model(model_name: str) -> int:
    target = (model_name or '').strip().lower()
    if not target:
        raise ValueError('empty flash_decision.model')
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from agentmain import GeneraticAgent

    agent = GeneraticAgent()
    candidates = []
    for idx, display, _cur in agent.list_llms():
        model = ''
        try:
            model = agent.get_llm_name(agent.llmclients[idx], model=True)
        except Exception:
            model = ''
        fields = [str(display), str(model)]
        candidates.append((idx, display, model))
        if any(target == f.lower() or target in f.lower() for f in fields if f):
            return int(idx)
    available = '; '.join(f'{i}:{d}|{m}' for i, d, m in candidates)
    raise RuntimeError(f'configured flash model not found: {model_name}; available={available}')


def context_lines(messages: List[Dict[str, Any]], limit: int = 16) -> str:
    out = []
    for m in (messages or [])[-limit:]:
        text = (m.get('text') or '').replace('\n', ' ').strip()
        if not text:
            continue
        # Prefer structured is_self so flash never treats our own bubble as a peer.
        # Peers keep display name; same nick as self still stays peer (not 我).
        if m.get('is_self'):
            sender = '我(本账号)'
            su = (m.get('sender_user') or '').strip()
            if su:
                sender = f'{sender}|账号={su}'
        else:
            nick = (m.get('sender') or '').strip() or '群友'
            su = (m.get('sender_user') or '').strip()
            sender = f'群友|{nick}' + (f'|账号={su}' if su else '')
        out.append(f"{sender}: {text}")
    return '\n'.join(out)


def text_for_trigger_scan(text: str) -> str:
    """Body + current-message [微信@] only; strip [微信引用] history.

    Quoted text may contain past @名字 / name hits. Those must not open
    direct-reference or keyword gates by themselves.
    """
    text = (text or '').strip()
    if not text:
        return ''
    at_lines = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith('[微信@]'):
            at_lines.append(s)
    if '[微信引用]' in text:
        body = text.split('[微信引用]', 1)[0].rstrip()
    else:
        body = text
    parts = []
    if body.strip():
        parts.append(body.strip())
    parts.extend(at_lines)
    return '\n'.join(parts)


def local_decide(req: Dict[str, Any], latest: Dict[str, Any]) -> Tuple[bool, str]:
    target = req.get('target') or {}
    kind = target.get('kind')
    raw_text = (latest.get('text') or '').strip()
    # Never scan quoted history for name/@/keyword triggers.
    text = text_for_trigger_scan(raw_text)
    display = ((req.get('agent') or {}).get('display_name') or '当前账号').strip()

    if kind == 'private':
        return True, 'flash_private_peer_message'

    direct_markers = [f'@{display}', display]
    direct_markers += list((target.get('rules') or {}).get('direct_markers') or [])
    if any(x and x in text for x in direct_markers):
        return True, 'flash_group_direct_reference'
    rules = target.get('rules') or {}
    if any((k or '').strip() and (k or '').strip() in text for k in (rules.get('keywords') or [])):
        return True, 'flash_group_keyword_context'
    return False, 'flash_group_not_about_agent'


def make_prompt(req: Dict[str, Any], latest: Dict[str, Any]) -> str:
    target = req.get('target') or {}
    gate = req.get('gate') or {}
    agent = req.get('agent') or {}
    state = req.get('state') or {}
    display = (agent.get('display_name') or '当前角色').strip()
    return format_prompt(
        'flash_decide.prompt',
        '',
        display=display,
        wechat_title=(target.get('wechat_title') or target.get('id') or ''),
        kind=(target.get('kind') or ''),
        session_key=(target.get('session_key') or agent.get('session_key') or ''),
        gate_reason=(gate.get('reason') or ''),
        mode=(state.get('mode') or ''),
        consecutive_skips=(state.get('consecutive_skips') or ''),
        context=context_lines(req.get('messages') or []),
        latest_sender=(latest.get('sender') or ''),
        latest_text=(latest.get('text') or '').strip(),
    )


@contextmanager
def _alarm_timeout(seconds: int, label: str):
    """Process-local SIGALRM timeout for blocking provider calls.

    ThreadPoolExecutor is not enough here: leaving its context waits for the
    worker thread, so Rust still sees a 70s adapter timeout.  SIGALRM interrupts
    the blocking direct LLM call and lets us emit structured provider_error.
    """
    deadline = max(1, int(seconds or 20))
    start = time.time()

    def _raise_timeout(_signum, _frame):
        raise subprocess.TimeoutExpired(
            cmd=label,
            timeout=deadline,
            output=f'elapsed={time.time() - start:.3f}s',
        )

    old_handler = signal.signal(signal.SIGALRM, _raise_timeout)
    old_timer = signal.setitimer(signal.ITIMER_REAL, deadline)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer and old_timer[0] > 0:
            # Restore any pre-existing timer best-effort.  This short-lived
            # adapter normally has none, but avoid clobbering callers.
            remaining = max(0.001, old_timer[0] - (time.time() - start))
            signal.setitimer(signal.ITIMER_REAL, remaining, old_timer[1])


def _run_llm_direct_with_timeout(prompt: str, llm_no: int, timeout: int) -> str:
    """Run direct one-shot LLM with an adapter-level timeout."""
    deadline = max(1, int(timeout or 20))
    with _alarm_timeout(deadline, f'run_llm_direct(llm_no={llm_no})'):
        return run_llm_direct(prompt, llm_no)


def flash_model_decide(req: Dict[str, Any], latest: Dict[str, Any]) -> Tuple[Optional[bool], str, Dict[str, Any]]:
    target = req.get('target') or {}
    extra = target.get('flash_decision') or target.get('flash') or {}
    model = str(extra.get('model') or os.environ.get('WECHAT_MULTI_FLASH_MODEL') or '').strip()
    llm_no_raw = extra.get('llm_no')
    if llm_no_raw is None:
        env_llm_no = os.environ.get('WECHAT_MULTI_FLASH_LLM_NO')
        llm_no_raw = env_llm_no if env_llm_no not in (None, '') else None
    if not model and llm_no_raw is None:
        return None, 'flash_model_not_configured', {'model': '', 'llm_no': None}
    timeout = int(extra.get('timeout') or os.environ.get('WECHAT_MULTI_FLASH_TIMEOUT') or 20)
    if llm_no_raw is not None:
        llm_no = int(llm_no_raw)
    else:
        llm_no = resolve_llm_no_by_model(model)

    prompt = make_prompt(req, latest)
    raw = _run_llm_direct_with_timeout(prompt, llm_no=llm_no, timeout=timeout)
    decision = parse_decision(raw)
    meta = {'model': model, 'llm_no': llm_no, 'timeout': timeout, 'raw_preview': extract_visible(raw)[:160]}
    if decision == 'SEND':
        return True, 'flash_model_send', meta
    if decision == 'SKIP':
        return False, 'flash_model_skip', meta
    return None, 'flash_model_parse_error', meta


def main():
    req = json.load(sys.stdin)
    msgs = req.get('messages') or []
    latest = latest_peer(msgs)
    if not latest:
        emit_result({'used': True, 'should_reply': False, 'reason': 'flash_no_peer_message'})
        return

    try:
        model_should, model_reason, meta = flash_model_decide(req, latest)
    except subprocess.TimeoutExpired as e:
        model_should, model_reason, meta = None, 'flash_model_timeout', {'error': repr(e)}
    except Exception as e:
        model_should, model_reason, meta = None, 'flash_model_error', {'error': repr(e)}

    if model_should is None:
        emit_result({
            'used': False,
            'reason': model_reason,
            'trigger_msg_id': latest.get('id'),
            'trigger_sender': latest.get('sender'),
            'flash_source': 'unavailable',
            'flash_meta': meta,
        })
        return

    should, reason, source = model_should, model_reason, 'model'

    emit_result({
        'used': True,
        'should_reply': should,
        'reason': reason,
        'trigger_msg_id': latest.get('id'),
        'trigger_sender': latest.get('sender'),
        'flash_source': source,
        'flash_meta': meta,
    })


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        emit_result({'used': False, 'adapter_error': repr(e)})