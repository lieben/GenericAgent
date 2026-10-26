#!/usr/bin/env python3
"""Export clean runtime side context for WeChat group generation.

Read-only helper for plugins/wechat_multi_chat_rust.  It must print pure JSON on
stdout and must not import clawbot/frontends entrypoints, because those plugins
emit install logs during import and would corrupt stdout for the Rust adapter.
"""
import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
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


def _fallback_time_context():
    now = datetime.now()
    return (
        '[真实时间与时间流逝感｜仅作时间锚点，不要外显]\n'
        f'- 当前本地时间：{now.strftime("%Y-%m-%d %H:%M")}。'
    )



def _parse_ts(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            val = float(raw)
            if val > 10_000_000_000:
                val /= 1000.0
            return datetime.fromtimestamp(val, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            if s.endswith('Z'):
                s = s[:-1] + '+00:00'
            if len(s) >= 5 and s[2] == ':' and len(s) <= 8:
                now = datetime.now().astimezone()
                parts = [int(x) for x in s.split(':')]
                while len(parts) < 3:
                    parts.append(0)
                return now.replace(hour=parts[0], minute=parts[1], second=parts[2], microsecond=0).astimezone(timezone.utc)
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    return None


def _item_ts(item):
    if not isinstance(item, dict):
        return None
    for key in ('ts', 'timestamp', 'time', 'created_at', 'created', 'msg_time', 'message_time'):
        if item.get(key) is not None:
            dt = _parse_ts(item.get(key))
            if dt is not None:
                return dt
    return None

def _content_to_text(content):
    """Extract visible text from GA native content blocks without tool/summary noise."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get('type') == 'text':
                    parts.append(str(item.get('text') or ''))
            elif isinstance(item, str):
                parts.append(item)
        return '\n'.join(x for x in parts if x)
    if isinstance(content, dict):
        return str(content.get('text') or '')
    return ''


def _clean_visible_text(text: str) -> str:
    """Keep only visible dialogue; runtime side context must not leak internals."""
    import re
    text = str(text or '')
    text = re.sub(r'<summary>.*?</summary>', '', text, flags=re.S | re.I)
    text = re.sub(r'```.*?```', '', text, flags=re.S)
    bad_markers = (
        '[运行时旁侧', '[最近群聊现场', '[最近私聊/主线', '[clawbot',
        '[SYSTEM:', 'WORKING MEMORY', '工具', 'stdout', 'stderr',
    )
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(m in stripped for m in bad_markers):
            continue
        lines.append(stripped)
    return ' '.join(lines).strip()


def _read_xinghui_main_recent_dialogue(recent_items=12, max_chars=1800, anchor_ts='', recent_minutes=15.0):
    """Read same-timeline xinghui:main backend_history for group side context.

    Only timestamped private/mainline turns within anchor_ts ± recent_minutes are
    injected.  If no anchor is supplied, or old history items have no timestamp,
    return empty instead of falling back to stale tail-count dialogue.
    """
    anchor = _parse_ts(anchor_ts)
    if anchor is None:
        return ''
    try:
        window = max(float(recent_minutes), 0.0)
    except Exception:
        window = 15.0
    delta = timedelta(minutes=window)
    start = anchor - delta
    end = anchor + delta

    state_path = ROOT / 'plugins' / 'local_frontends' / 'runtime' / 'state' / 'wechat_session_state.json'
    if not state_path.exists():
        return ''
    try:
        data = json.loads(state_path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return ''
    session = data.get('xinghui:main') or {}
    history = session.get('backend_history') or []
    rows = []
    for item in history:
        if not isinstance(item, dict):
            continue
        item_dt = _item_ts(item)
        if item_dt is None or item_dt < start or item_dt > end:
            continue
        role = str(item.get('role') or '')
        if role not in {'user', 'assistant'}:
            continue
        text = _clean_visible_text(_content_to_text(item.get('content')))
        if not text:
            continue
        speaker = str(get_prompt_config('export_group_runtime_context.speaker_user', 'user')) if role == 'user' else str(get_prompt_config('export_group_runtime_context.speaker_assistant', 'assistant'))
        rows.append((item_dt, f'- {speaker}: {text[:260]}'))
    rows = [row for _, row in sorted(rows, key=lambda x: x[0])][-recent_items:]
    if not rows:
        return ''
    body = '\n'.join(rows)
    if len(body) > max_chars:
        body = body[-max_chars:]
    return format_prompt(
        'export_group_runtime_context.private_side_context',
        '[运行时旁侧：同时间线私聊完整对白｜anchor={{anchor}} ±{{window}}分钟]\n'
        '**用途**：仅用于状态对齐，让当前角色知道自己和对话对象的最新状态、行动承诺和上下文连续性，避免说出矛盾内容。\n'
        '**边界**：不要默认把这里的话题带到群聊继续聊；只有当群聊现场也在聊同一话题，或对话对象在群里主动提起时，才可自然呼应。\n'
        '**来源**：主会话 backend_history 中带时间戳且落在本轮群聊同时间线窗口内的可见对白（完整非摘要）。\n'
        '{{body}}',
        anchor=anchor.isoformat(), window=f'{window:g}', body=body
    )


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--anchor-ts', default='')
    parser.add_argument('--recent-minutes', type=float, default=15.0)
    args, _ = parser.parse_known_args()
    data = {
        'time_context': _fallback_time_context(),
        'private_focus': '',
        'clawbot_focus': _read_xinghui_main_recent_dialogue(anchor_ts=args.anchor_ts, recent_minutes=args.recent_minutes),
    }
    print(json.dumps(data, ensure_ascii=False))


if __name__ == '__main__':
    main()
