"""GA Windows physical WeChat adapter. No pyautogui."""
from __future__ import annotations
import json, os, subprocess, time
from pathlib import Path
import win32con, win32gui

PLUGIN_ROOT = Path(os.environ.get("PRIVATE_MONITOR_ROOT") or Path(__file__).resolve().parent)
PENDING_FILE = PLUGIN_ROOT / "pending_send.json"
RESULT_FILE = PLUGIN_ROOT / "send_result.json"

# Wait budget for the interactive runner.
# Measured on Win176 (2026-10-21): 1 bubble ~1s, 3 ~4s, 4 ~5-6s, 5 ~9s.
# Each extra bubble costs ~1.8s (PowerShell clipboard + paste + 0.45s gap),
# so a fixed 8s deadline times out on 5+ bubbles and triggers a duplicate resend.
BASE_WAIT_SECONDS = 8.0
SEGMENT_WAIT_SECONDS = 1.8
MAX_WAIT_SECONDS = 45.0


def _count_segments(text: str) -> int:
    """Count bubbles the runner will send.

    Must mirror split_segments() in wechat_task_runner.py: split on blank
    lines when present, otherwise on single newlines; drop empty parts.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not raw.strip():
        return 0
    parts = raw.split("\n\n") if "\n\n" in raw else raw.split("\n")
    return sum(1 for p in parts if p.strip())


def _wait_seconds_for(segment_count: int) -> float:
    extra = max(0, segment_count - 1) * SEGMENT_WAIT_SECONDS
    return min(MAX_WAIT_SECONDS, BASE_WAIT_SECONDS + extra)


def _read_result():
    if not RESULT_FILE.exists():
        return None
    try:
        with open(RESULT_FILE, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception:
        return None


def send_text(title: str, text: str, *, mention: str='', contact: str=''):
    text = str(text or '').strip()
    if not text:
        return {'sent': False, 'reason': 'empty_reply'}

    target = str(contact or title or '').strip()
    segments = _count_segments(text)
    wait_seconds = _wait_seconds_for(segments)

    payload = {
        'title': target,
        'text': text,
        'mention': mention,
        'time': time.time()
    }

    if RESULT_FILE.exists():
        try:
            RESULT_FILE.unlink()
        except Exception:
            pass

    with open(PENDING_FILE, 'w', encoding='utf-8-sig') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    subprocess.run(['schtasks', '/run', '/tn', 'SendTestTask'], capture_output=True, timeout=10)

    deadline = time.time() + wait_seconds
    res_data = None
    while time.time() < deadline:
        res_data = _read_result()
        if res_data is not None:
            break
        time.sleep(0.3)

    # Final check: the runner may have finished just after the deadline.
    if res_data is None:
        res_data = _read_result()

    if res_data and res_data.get('status') == 'success':
        return {
            'sent': True,
            'target': target,
            'contact': target,
            'segments_expected': segments,
            'wait_seconds': wait_seconds,
            'res': res_data,
        }

    err = res_data.get('error') if res_data else 'timeout_waiting_for_task'
    raise RuntimeError(
        f'Physical send failed: {err} (segments={segments}, waited={wait_seconds:.1f}s)'
    )
