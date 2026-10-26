#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import queue
import random
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from prompt_config import format_prompt
except Exception:
    def format_prompt(_key: str, default: str = '', **values: Any) -> str:
        out = default
        for k, v in values.items():
            out = out.replace('{{' + k + '}}', str(v))
        return out

try:
    from wx_plus_runtime_context import _load_state as _rc_load_state, _fmt_elapsed
except Exception:
    _rc_load_state = dict
    _fmt_elapsed = lambda x: ""

try:
    from wx_group_shadow_reader import known_titles, get_unsummarized_lines, format_for_context
except Exception:
    known_titles = lambda: []
    get_unsummarized_lines = lambda title, n=30: []
    format_for_context = lambda records, title: ""

try:
    from session_history import (
        _visible_dialog_only,
        get_backend_history,
        set_backend_history,
        _content_to_text_blocks,
    )
except Exception:
    _visible_dialog_only = lambda text: str(text or '').strip()
    get_backend_history = lambda agent: []
    set_backend_history = lambda agent, hist: False
    _content_to_text_blocks = lambda text: [{'type': 'text', 'text': str(text or '')}]

try:
    from shared_chat_runtime import restore_frontend_session, persist_frontend_session
except Exception:
    restore_frontend_session = None
    persist_frontend_session = None

_APP = None
_LOCK = threading.RLock()
_STATE_FILE = 'wx_plus_proactive_state.json'
_STARTED = False
_PROACTIVE_EVENT_RETRY_DELAY_SECONDS = 5 * 60
# Proactive topics are time-sensitive.  Never generate/send a stale topic after
# the conversation context that produced it has gone cold.
_PROACTIVE_EVENT_MAX_AGE_SECONDS = int(os.environ.get('WECHAT_PLUS_PROACTIVE_EVENT_MAX_AGE_SEC', str(24 * 60 * 60)))
_PRIVATE_TARGET_MAX_AGE_SECONDS = int(os.environ.get('WECHAT_PLUS_PRIVATE_TARGET_MAX_AGE_SECONDS', str(7 * 24 * 60 * 60)))


def _is_send_ok(ret: Any) -> bool:
    """True when clawbot/bot send_text reports a successful delivery.

    Historical success shapes observed in production:
    - ``{}``
    - ``{'ret': 0}``
    - ``{'message_id': <int/str>}``  (actual delivery ack; previously misread as failure
      and caused cached proactive-event text to resend every retry window)
    Explicit non-zero ``ret`` codes remain failures even if other fields exist.
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


def _private_target_is_fresh(st: Dict[str, Any], now: Optional[float] = None) -> Tuple[bool, str]:
    """Return whether the cached clawbot private target is safe to send to."""
    now = _now() if now is None else float(now)
    uid = str(st.get('last_seen_uid') or '').strip()
    token = str(st.get('last_seen_context_token') or '').strip()
    if not uid:
        return False, 'missing_uid'
    if not token:
        return False, 'missing_context_token'
    try:
        last_inbound_ts = float(st.get('last_inbound_ts') or 0)
    except Exception:
        last_inbound_ts = 0.0
    if last_inbound_ts <= 0:
        return False, 'missing_last_inbound_ts'
    age = now - last_inbound_ts
    if age > _PRIVATE_TARGET_MAX_AGE_SECONDS:
        return False, f'stale_last_seen age={int(age)}s max={_PRIVATE_TARGET_MAX_AGE_SECONDS}s'
    if age < -60:
        return False, f'future_last_seen age={int(age)}s'
    return True, 'ok'


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _state_path() -> Path:
    p = _root() / 'temp' / 'wechat_state'
    p.mkdir(parents=True, exist_ok=True)
    return p / _STATE_FILE


def _notify_proactive_failure(kind: str, detail: str) -> None:
    """Send one WxPusher alert per distinct proactive-channel failure state."""
    fingerprint = f'{str(kind or "unknown")}:{str(detail or "")[:500]}'
    with _LOCK:
        st = _load_state()
        if st.get('proactive_alert_active') and st.get('proactive_alert_key') == fingerprint:
            return
        st['proactive_alert_active'] = True
        st['proactive_alert_key'] = fingerprint
        st['proactive_alert_ts'] = _now()
        _save_state(st)
    try:
        from plugins.wxpusher_notify import send_notification
        result = send_notification(
            '微信主动留言通道发生错误，请安排时间检查。\n'
            f'类型：{kind}\n详情：{str(detail or "")[:500]}'
        )
        print(f'[WX+ proactive] failure alert sent result={result!r}', file=sys.__stdout__)
    except Exception as e:
        print(f'[WX+ proactive] failure alert error: {type(e).__name__}: {e}', file=sys.__stdout__)


def _notify_proactive_recovered() -> None:
    """Notify once when a previously alerted proactive channel recovers."""
    with _LOCK:
        st = _load_state()
        if not st.get('proactive_alert_active'):
            return
        st.pop('proactive_alert_active', None)
        st.pop('proactive_alert_key', None)
        st['proactive_alert_recovered_ts'] = _now()
        _save_state(st)
    try:
        from plugins.wxpusher_notify import send_notification
        result = send_notification('微信主动留言通道已恢复。')
        print(f'[WX+ proactive] recovery alert sent result={result!r}', file=sys.__stdout__)
    except Exception as e:
        print(f'[WX+ proactive] recovery alert error: {type(e).__name__}: {e}', file=sys.__stdout__)


def _read_proactive_events(limit: int = 1) -> List[Dict[str, Any]]:
    """Read at most one unique proactive-event queue entry without deleting it.

    Entries are acknowledged only after clawbot send_text returns, so a transient
    generation/send/logging failure cannot silently swallow the event.  The queue
    may receive duplicate topics from repeated group-side generations; collapse
    same topic/source/target/title while reading so one loop cannot fan out into
    multiple private sends.
    """
    path = _proactive_event_queue_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except Exception as e:
        print(f'[WX+ proactive] proactive event queue read error: {e}', file=sys.__stdout__)
        return []

    def _queue_dedupe_key(obj: Dict[str, Any]) -> str:
        return json.dumps({
            'source': str(obj.get('source') or ''),
            'target_id': str(obj.get('target_id') or ''),
            'title': str(obj.get('title') or ''),
            'topic': str(obj.get('topic') or obj.get('event') or '').strip(),
        }, ensure_ascii=False, sort_keys=True)

    items: List[Dict[str, Any]] = []
    seen = set()
    keep: List[str] = []
    changed = False
    for idx, line in enumerate(lines):
        if not line.strip():
            changed = True
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                key = _queue_dedupe_key(obj)
                if key in seen:
                    changed = True
                    continue
                seen.add(key)
                keep.append(line)
                if len(items) < max(1, int(limit)):
                    obj['_queue_line_index'] = idx
                    obj['_queue_raw_line'] = line
                    items.append(obj)
                continue
        except Exception as e:
            print(f'[WX+ proactive] proactive event queue bad json line={idx}: {e}', file=sys.__stdout__)
        keep.append(line)
    if changed:
        try:
            tmp = path.with_suffix('.tmp')
            if keep:
                tmp.write_text('\n'.join(keep) + '\n', encoding='utf-8')
                tmp.replace(path)
            else:
                path.unlink(missing_ok=True)
            print(f'[WX+ proactive] proactive-event queue deduped old={len(lines)} new={len(keep)}', file=sys.__stdout__)
        except Exception as e:
            print(f'[WX+ proactive] proactive event queue dedupe error: {type(e).__name__}: {e}', file=sys.__stdout__)
    return items


def _ack_proactive_event(event: Dict[str, Any]) -> None:
    path = _proactive_event_queue_path()
    raw = str((event or {}).get('_queue_raw_line') or '')
    if not raw or not path.exists():
        return
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
        removed = False
        keep: List[str] = []
        for line in lines:
            if not removed and line == raw:
                removed = True
                continue
            keep.append(line)
        tmp = path.with_suffix('.tmp')
        if keep:
            tmp.write_text('\n'.join(keep) + '\n', encoding='utf-8')
            tmp.replace(path)
        else:
            path.unlink(missing_ok=True)
        print(f'[WX+ proactive] proactive-event ack removed={removed}', file=sys.__stdout__)
    except Exception as e:
        print(f'[WX+ proactive] proactive event queue ack error: {type(e).__name__}: {e}', file=sys.__stdout__)


def _cache_proactive_event_reply(event: Dict[str, Any], text: str) -> None:
    path = _proactive_event_queue_path()
    raw = str((event or {}).get('_queue_raw_line') or '')
    if not raw or not text or not path.exists():
        return
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
        updated = False
        keep: List[str] = []
        for line in lines:
            if not updated and line == raw:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    obj['cached_reply_text'] = text
                    obj['cached_reply_ts'] = _now()
                    keep.append(json.dumps(obj, ensure_ascii=False, sort_keys=True))
                    updated = True
                    continue
            keep.append(line)
        if updated:
            tmp = path.with_suffix('.tmp')
            tmp.write_text('\n'.join(keep) + '\n', encoding='utf-8')
            tmp.replace(path)
        print(f'[WX+ proactive] proactive-event cache reply updated={updated}', file=sys.__stdout__)
    except Exception as e:
        print(f'[WX+ proactive] proactive event queue cache reply error: {type(e).__name__}: {e}', file=sys.__stdout__)


def _proactive_event_fallback_text(event: Dict[str, Any]) -> str:
    return str((event or {}).get('topic') or event.get('event') or '').strip()


def _proactive_event_key(event: Dict[str, Any]) -> str:
    raw = str((event or {}).get('_queue_raw_line') or '').strip()
    if raw:
        return raw
    try:
        return json.dumps(event or {}, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(event or {})


def _proactive_event_retry_blocked(st: Dict[str, Any], event: Dict[str, Any], now: float) -> bool:
    retry_after = float(st.get('last_proactive_event_retry_after') or 0)
    if retry_after <= now:
        return False
    failed_key = st.get('last_proactive_event_failed_key')
    if not failed_key:
        return False
    if failed_key == _proactive_event_key(event):
        return True
    return True


def _mark_proactive_event_send_failed(event: Dict[str, Any], text: str, ret: Any) -> None:
    with _LOCK:
        st = _load_state()
        now = _now()
        st['last_proactive_event_failed_key'] = _proactive_event_key(event)
        st['last_proactive_event_retry_after'] = now + _PROACTIVE_EVENT_RETRY_DELAY_SECONDS
        st['last_proactive_event_failed_ts'] = now
        st['last_proactive_event_failed_text'] = text
        st['last_proactive_event_failed_ret'] = repr(ret)
        _save_state(st)


def _record_proactive_visible_turn(prompt: str, text: str) -> bool:
    """Persist a proactive clawbot send as one visible assistant turn.

    ``agent.put_task(prompt)`` already lets Xinghui generate through the shared
    backend, but the stored user side is an internal control prompt rather than
    a message Yanyan actually sent.  After the bot API send succeeds, rewrite the
    tail into a clean visible assistant message so the next clawbot/API turn sees
    that Xinghui has already said it, without leaking the control prompt.
    """
    app = _APP
    if app is None or not text:
        return False
    try:
        agent = getattr(app, 'agent', None)
        hist = list(get_backend_history(agent)) if agent is not None else []
        if not isinstance(hist, list):
            hist = []
        visible_text = _visible_dialog_only(text).strip()
        if not visible_text:
            return False

        # Drop the internal task prompt that caused this proactive send, if it
        # is still the last user message.  This keeps xinghui:main as visible
        # dialogue instead of storing PROACTIVE_EVENT/control instructions.
        prompt_s = str(prompt or '').strip()
        while hist:
            last = hist[-1] if isinstance(hist[-1], dict) else None
            if not last:
                hist.pop()
                continue
            role = last.get('role')
            content_text = ''.join(
                str(block.get('text') or '') for block in last.get('content', [])
                if isinstance(block, dict)
            ) if isinstance(last.get('content'), list) else str(last.get('content') or '')
            content_text = content_text.strip()
            if role == 'user' and (content_text == prompt_s or 'PROACTIVE_EVENT' in content_text or 'proactive proactive event' in content_text.lower()):
                hist.pop()
                continue
            if role == 'assistant' and _visible_dialog_only(content_text).strip() == visible_text:
                hist.pop()
                continue
            break

        hist.append({'role': 'assistant', 'content': _content_to_text_blocks(visible_text)})
        ok = set_backend_history(agent, hist)
        if ok:
            print(f'[WX+ proactive] recorded visible proactive turn text_len={len(visible_text)}', file=sys.__stdout__)
        return bool(ok)
    except Exception as e:
        print(f'[WX+ proactive] record visible turn failed: {type(e).__name__}: {e}', file=sys.__stdout__)
        return False


def _build_proactive_event_prompt(event: Dict[str, Any]) -> str:
    topic = str((event or {}).get('topic') or event.get('event') or '').strip()
    title = str((event or {}).get('title') or '').strip() or '未知来源'
    source = str((event or {}).get('source') or '').strip() or 'unknown'
    context = str((event or {}).get('context') or '').strip()
    now_text = datetime.now().strftime('%Y-%m-%d %H:%M')
    return format_prompt('proactive.event', '', now_text=now_text, title=title, source=source, topic=topic, context=context)


def _copy_backend_history(hist: Any) -> List[Dict[str, Any]]:
    if not isinstance(hist, list):
        return []
    try:
        import copy
        return copy.deepcopy(hist)
    except Exception:
        try:
            return json.loads(json.dumps(hist, ensure_ascii=False))
        except Exception:
            return list(hist)


def _snapshot_backend_history() -> List[Dict[str, Any]]:
    app = _APP
    if app is None:
        return []
    try:
        agent = getattr(app, 'agent', None)
        return _copy_backend_history(get_backend_history(agent) if agent is not None else [])
    except Exception as e:
        print(f'[WX+ proactive] history snapshot failed (non-fatal): {type(e).__name__}: {e}', file=sys.__stdout__)
        return []


def _restore_backend_history_snapshot(snapshot: List[Dict[str, Any]], reason: str = '') -> bool:
    app = _APP
    if app is None:
        return False
    try:
        agent = getattr(app, 'agent', None)
        ok = set_backend_history(agent, _copy_backend_history(snapshot)) if agent is not None else False
        if ok and persist_frontend_session is not None:
            persist_frontend_session(agent, 'xinghui:main')
        if ok:
            suffix = f' reason={reason}' if reason else ''
            print(f'[WX+ proactive] restored backend_history snapshot n={len(snapshot)}{suffix}', file=sys.__stdout__)
        return bool(ok)
    except Exception as e:
        print(f'[WX+ proactive] history snapshot restore failed: {type(e).__name__}: {e}', file=sys.__stdout__)
        return False


def _load_state() -> Dict[str, Any]:
    p = _state_path()
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_state(st: Dict[str, Any]) -> None:
    p = _state_path()
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(p)


def _now() -> float:
    return time.time()


def _rand_gap_seconds() -> int:
    lo = int(os.environ.get('WECHAT_PLUS_PROACTIVE_MIN_GAP_SEC', '21600'))
    hi = int(os.environ.get('WECHAT_PLUS_PROACTIVE_MAX_GAP_SEC', '64800'))
    lo = max(1800, lo)
    hi = max(lo + 60, hi)
    return random.randint(lo, hi)


def _within_awake_window(ts: Optional[float] = None) -> bool:
    ts = _now() if ts is None else ts
    dt = datetime.fromtimestamp(ts)
    start_h = int(os.environ.get('WECHAT_PLUS_PROACTIVE_WAKE_START_HOUR', '10'))
    end_h = int(os.environ.get('WECHAT_PLUS_PROACTIVE_WAKE_END_HOUR', '23'))
    if start_h <= end_h:
        return start_h <= dt.hour <= end_h
    return dt.hour >= start_h or dt.hour <= end_h


def _extract_uid_ctx(ctx: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    raw = ctx.get('raw_msg') if isinstance(ctx, dict) else None
    uid = None
    token = None
    if isinstance(raw, dict):
        uid = raw.get('from_user_id') or raw.get('uid') or raw.get('open_id') or raw.get('chat_id')
        token = raw.get('context_token') or raw.get('chat_id') or raw.get('conversation_id')
    return (str(uid).strip() if uid else None, str(token).strip() if token else None)


def _active_high_group_titles() -> List[str]:
    """Return wechat titles whose Rust monitor runtime is currently high.

    Low-frequency groups mean Xinghui is not actively watching/participating, so
    proactive wakeups must not inspect them as background sources.  This reads
    only the monitor's runtime mode and target config; it does not poll chats or
    trigger any group activity.
    """
    rust_root = Path(__file__).resolve().parent.parent / 'wechat_multi_chat_rust'
    state_path = rust_root / 'runtime' / 'state.json'
    config_path = rust_root / 'config' / 'config.json'
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
        cfg = json.loads(config_path.read_text(encoding='utf-8'))
    except Exception:
        return []
    targets_state = state.get('targets') if isinstance(state, dict) else None
    targets_cfg = cfg.get('targets') if isinstance(cfg, dict) else None
    if not isinstance(targets_state, dict) or not isinstance(targets_cfg, list):
        return []
    titles: List[str] = []
    for target in targets_cfg:
        if not isinstance(target, dict):
            continue
        if target.get('kind') != 'group' or target.get('enabled') is False:
            continue
        tid = str(target.get('id') or '').strip()
        title = str(target.get('wechat_title') or '').strip()
        st = targets_state.get(tid)
        if title and isinstance(st, dict) and st.get('mode') == 'high':
            titles.append(title)
    known = set(known_titles())
    return [t for t in titles if t in known]


def _recent_group_hint() -> str:
    """Return optional same-period high-frequency group context.

    This is a topic source sampled only after an autonomous wake has already
    happened.  It must never become an event trigger.  Only groups currently in
    Rust runtime `mode=high` are inspected; low-frequency groups are invisible
    here because Xinghui is not watching/participating in them by default.
    """
    try:
        if not bool(_rc_load_state().get('group_awareness_enabled', True)):
            return ''
    except Exception:
        pass
    titles = _active_high_group_titles()
    if not titles:
        return ''
    blocks = []
    for title in titles:
        block = format_for_context(get_unsummarized_lines(title, n=30), title)
        if block:
            blocks.append(block)
    if not blocks:
        return ''
    return format_prompt('proactive.recent_group_hint', '', blocks='\n\n'.join(blocks))


def _daily_sources() -> List[str]:
    """Collect optional daily-life sources for autonomous proactive thinking.

    Future sources (reading, web browsing, study notes, etc.) can be appended
    here.  Returning an empty list is valid and should produce a blank-missing
    style wake rather than blocking proactive contact.
    """
    sources: List[str] = []
    hint = _recent_group_hint()
    if hint:
        sources.append(hint)
    return sources


def _build_autonomous_wake_prompt(st: Dict[str, Any]) -> str:
    sources = _daily_sources()
    try:
        now_text = datetime.now().strftime('%Y-%m-%d %H:%M')
    except Exception:
        now_text = ''
    try:
        rc = _rc_load_state()
        last_msg_ts = float(rc.get('last_msg_ts') or st.get('last_inbound_ts') or time.time())
        elapsed = _fmt_elapsed(time.time() - last_msg_ts)
    except Exception:
        elapsed = ''
    source_block = ''
    if sources:
        source_block = '\n\n'.join(sources)
    else:
        source_block = format_prompt('proactive.autonomous_wake_no_sources', '')
    return format_prompt('proactive.autonomous_wake', '', now_text=now_text, elapsed=(elapsed or '未知'), source_block=source_block)

def _generate_proactive_reply(uid: str, token: str, prompt: str) -> str:
    """Generate a proactive message through the same clawbot GA agent/session.

    The plugin only schedules an autonomous wake opportunity.  Visible text is
    always generated through the shared xinghui:main task queue, so clawbot stays
    an input/output frontend rather than a separate speaker or template sender.
    """
    app = _APP
    if app is None:
        return ''
    if hasattr(app, 'last_uid'):
        app.last_uid = uid or getattr(app, 'last_uid', '')
    if hasattr(app, 'last_context_token'):
        app.last_context_token = token or getattr(app, 'last_context_token', '')
    try:
        if restore_frontend_session is not None:
            restore_frontend_session(app.agent, 'xinghui:main', force=True)
            print('[WX+ proactive] restored session before put_task', file=sys.__stdout__)
    except Exception as e:
        print(f'[WX+ proactive] restore failed (non-fatal): {type(e).__name__}: {e}', file=sys.__stdout__)
    try:
        dq = app.agent.put_task(prompt, source='wechat_proactive')
    except TypeError:
        dq = app.agent.put_task(prompt)
    item: Dict[str, Any] = {}
    try:
        while True:
            item = dq.get(timeout=300)
            if 'done' in item:
                break
    except queue.Empty:
        print('[WX+ proactive] agent timeout')
        return ''

    # GA's final reply is in ``done``, which may include internal blocks (summary,
    # thinking, tool logs, etc.) alongside user-visible dialogue.  Apply the shared
    # visible_dialog_only sanitizer to extract only the clean text that should reach
    # the user.  If sanitization yields empty or a bare control tail like
    # "### NEXT ACTION", skip sending rather than exposing internal state.
    done_text = str(item.get('done') or '').strip()
    cleaned = _visible_dialog_only(done_text)
    if not cleaned or cleaned.strip().upper().startswith('### NEXT ACTION'):
        return ''
    return cleaned

def _record_contact(msg: Any) -> None:
    ctx = {'raw_msg': msg} if isinstance(msg, dict) else {}
    uid, token = _extract_uid_ctx(ctx)
    if not uid:
        return
    with _LOCK:
        st = _load_state()
        st['last_seen_uid'] = uid
        if token:
            st['last_seen_context_token'] = token
        st['last_inbound_ts'] = _now()
        if not st.get('next_due_ts'):
            st['next_due_ts'] = _now() + _rand_gap_seconds()
        _save_state(st)
    print(f'[WX+ proactive] inbound contact recorded uid={uid} token_present={bool(token)}', file=sys.__stdout__)


def _after_put_task(*_args: Any) -> None:
    with _LOCK:
        st = _load_state()
        st['last_model_turn_ts'] = _now()
        _save_state(st)


def _loop() -> None:
    global _APP
    while True:
        time.sleep(30)
        if _APP is None:
            continue
        try:
            events = _read_proactive_events()
            if events:
                print(f'[WX+ proactive] proactive-event start count={len(events)}', file=sys.__stdout__)
                with _LOCK:
                    st = _load_state()
                    uid = st.get('last_seen_uid')
                    token = st.get('last_seen_context_token')
                if not uid:
                    print(f'[WX+ proactive] proactive-event waiting: no last_seen_uid count={len(events)}', file=sys.__stdout__)
                else:
                    now = _now()
                    fresh, stale_reason = _private_target_is_fresh(st, now)
                    if not fresh:
                        print(f'[WX+ proactive] proactive-event waiting: stale clawbot private target reason={stale_reason} uid={uid} count={len(events)}', file=sys.__stdout__)
                        continue
                    for event in events:
                        now = _now()
                        event_ts = float((event or {}).get('ts') or (event or {}).get('created_ts') or 0)
                        if event_ts > 0 and now - event_ts > _PROACTIVE_EVENT_MAX_AGE_SECONDS:
                            _ack_proactive_event(event)
                            print(f'[WX+ proactive] dropped stale event age={int(now-event_ts)}s', file=sys.__stdout__)
                            continue
                        with _LOCK:
                            st = _load_state()
                            retry_blocked = _proactive_event_retry_blocked(st, event, now)
                            retry_after = float(st.get('last_proactive_event_retry_after') or 0)
                        if retry_blocked:
                            remain = max(0, int(retry_after - now))
                            print(f'[WX+ proactive] proactive-event retry delayed remain={remain}s; keep queued uid={uid}', file=sys.__stdout__)
                            continue
                        cached_text = str((event or {}).get('cached_reply_text') or '').strip()
                        if cached_text:
                            prompt = ''
                            text = cached_text
                            history_snapshot = None
                            print(f'[WX+ proactive] proactive-event reuse cached reply uid={uid} token={token!r} text_len={len(text)}', file=sys.__stdout__)
                        else:
                            prompt = _build_proactive_event_prompt(event)
                            text = ''
                            history_snapshot = _snapshot_backend_history()
                            try:
                                text = _generate_proactive_reply(uid, token, prompt)
                            except Exception as e:
                                print(f'[WX+ proactive] proactive-event generate error: {type(e).__name__}: {e}', file=sys.__stdout__)
                            if not text:
                                _restore_backend_history_snapshot(history_snapshot, 'empty_private_event_generation')
                                text = _proactive_event_fallback_text(event)
                                print(f'[WX+ proactive] proactive-event fallback raw event uid={uid} token={token!r}', file=sys.__stdout__)
                        if not text:
                            _restore_backend_history_snapshot(history_snapshot, 'empty_private_event_fallback')
                            print(f'[WX+ proactive] proactive-event empty fallback; keep queued uid={uid} token={token!r}', file=sys.__stdout__)
                            continue
                        send_ok = False
                        send_context_token = token or ''
                        try:
                            print(f'[WX+ proactive] sending proactive-event uid={uid} gen_token={token!r} send_token={send_context_token!r} text_len={len(text)}', file=sys.__stdout__)
                            ret = _APP.bot.send_text(uid, text, context_token=send_context_token)
                            print(f'[WX+ proactive] send_text returned proactive-event uid={uid} ret={ret!r}', file=sys.__stdout__)
                            send_ok = _is_send_ok(ret)
                        except Exception as e:
                            _restore_backend_history_snapshot(history_snapshot, 'private_event_send_exception')
                            _cache_proactive_event_reply(event, text)
                            _mark_proactive_event_send_failed(event, text, f'{type(e).__name__}: {e}')
                            _notify_proactive_failure('微信主动事件发送异常', f'{type(e).__name__}: {e}')
                            print(f'[WX+ proactive] proactive-event send error: {type(e).__name__}: {e}; keep queued and cool down queue', file=sys.__stdout__)
                            break
                        if not send_ok:
                            _restore_backend_history_snapshot(history_snapshot, 'private_event_send_failed')
                            ret_text = str(ret or '').lower()
                            if 'prepare failed' in ret_text or (isinstance(ret, dict) and ret.get('ret') == -2):
                                _ack_proactive_event(event)
                                _notify_proactive_failure('微信主动事件发送失败', repr(ret))
                                print(f'[WX+ proactive] dropped unrecoverable send failure ret={ret!r}', file=sys.__stdout__)
                                continue
                            _cache_proactive_event_reply(event, text)
                            _mark_proactive_event_send_failed(event, text, ret)
                            _notify_proactive_failure('微信主动事件发送失败', repr(ret))
                            print(f'[WX+ proactive] proactive-event send not ok; keep queued and cool down queue ret={ret!r}', file=sys.__stdout__)
                            break
                        try:
                            _record_proactive_visible_turn(prompt, text)
                            if persist_frontend_session is not None:
                                persist_frontend_session(_APP.agent, 'xinghui:main')
                        except Exception as e:
                            print(f'[WX+ proactive] persist failed (non-fatal): {type(e).__name__}: {e}', file=sys.__stdout__)
                        with _LOCK:
                            st = _load_state()
                            st['last_private_event_ts'] = _now()
                            st['last_private_event_text'] = text
                            st['last_private_event_prompt_preview'] = prompt[:500]
                            st.pop('last_proactive_event_failed_key', None)
                            st.pop('last_proactive_event_retry_after', None)
                            st.pop('last_proactive_event_failed_ts', None)
                            st.pop('last_proactive_event_failed_text', None)
                            st.pop('last_proactive_event_failed_ret', None)
                            _save_state(st)
                        _ack_proactive_event(event)
                        _notify_proactive_recovered()
                        print(f'[WX+ proactive] sent proactive-event to uid={uid} token={token!r}', file=sys.__stdout__)
                continue
            with _LOCK:
                st = _load_state()
                now = _now()
                if not os.environ.get('WECHAT_PLUS_PROACTIVE_ENABLE', '1').strip().lower() in {'1', 'true', 'yes', 'on'}:
                    continue
                uid = st.get('last_seen_uid')
                if not uid:
                    continue
                due = float(st.get('next_due_ts') or 0)
                if due <= 0:
                    st['next_due_ts'] = now + _rand_gap_seconds()
                    _save_state(st)
                    continue
                if now < due or not _within_awake_window(now):
                    continue
                prompt = _build_autonomous_wake_prompt(st)
                token = st.get('last_seen_context_token')
                st['last_proactive_ts'] = now
                st['last_proactive_prompt_preview'] = prompt[:500]
                st['proactive_count'] = int(st.get('proactive_count') or 0) + 1
                st['next_due_ts'] = now + _rand_gap_seconds()
                _save_state(st)
            history_snapshot = _snapshot_backend_history()
            text = _generate_proactive_reply(uid, token, prompt)
            if not text:
                _restore_backend_history_snapshot(history_snapshot, 'empty_proactive_generation')
                print(f'[WX+ proactive] empty generated reply uid={uid} token={token!r}')
                continue
            send_context_token = token or ''
            send_ok = False
            try:
                print(f'[WX+ proactive] sending generated uid={uid} gen_token={token!r} send_token={send_context_token!r} text_len={len(text)}', file=sys.__stdout__)
                ret = _APP.bot.send_text(uid, text, context_token=send_context_token)
                print(f'[WX+ proactive] send_text returned generated uid={uid} ret={ret!r}', file=sys.__stdout__)
                send_ok = _is_send_ok(ret)
            except Exception as e:
                _restore_backend_history_snapshot(history_snapshot, 'proactive_send_exception')
                _notify_proactive_failure('微信主动留言发送异常', f'{type(e).__name__}: {e}')
                print(f'[WX+ proactive] generated send error: {type(e).__name__}: {e}', file=sys.__stdout__)
                continue
            if not send_ok:
                _restore_backend_history_snapshot(history_snapshot, 'proactive_send_failed')
                with _LOCK:
                    st = _load_state()
                    st['last_proactive_failed_ts'] = _now()
                    st['last_proactive_failed_text'] = text
                    st['last_proactive_failed_ret'] = repr(ret)
                    _save_state(st)
                _notify_proactive_failure('微信主动留言发送失败', repr(ret))
                print(f'[WX+ proactive] generated send not ok; skip history persist ret={ret!r}', file=sys.__stdout__)
                continue
            try:
                _record_proactive_visible_turn(prompt, text)
                if persist_frontend_session is not None:
                    persist_frontend_session(_APP.agent, 'xinghui:main')
            except Exception as e:
                print(f'[WX+ proactive] persist failed (non-fatal): {type(e).__name__}: {e}', file=sys.__stdout__)
            with _LOCK:
                st = _load_state()
                st['last_proactive_text'] = text
                st.pop('last_proactive_failed_ts', None)
                st.pop('last_proactive_failed_text', None)
                st.pop('last_proactive_failed_ret', None)
                _save_state(st)
            print(f'[WX+ proactive] sent generated to uid={uid} gen_token={token!r} send_token={send_context_token!r}')
            _notify_proactive_recovered()
        except Exception as e:
            print(f'[WX+ proactive] loop error: {type(e).__name__}: {e}', file=sys.__stdout__)


def install(app):
    global _APP, _STARTED
    _APP = app
    app.register_hook('after_message', _record_contact)
    app.register_hook('after_put_task', _after_put_task)
    if not _STARTED:
        _STARTED = True
        threading.Thread(target=_loop, daemon=True).start()
    print('[WX+ proactive] installed')
