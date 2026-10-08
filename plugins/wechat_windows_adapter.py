"""GA Windows physical WeChat adapter. No pyautogui."""
from __future__ import annotations
import json, os, subprocess, time
from pathlib import Path
import win32con, win32gui

PLUGIN_ROOT = Path(r"C:\Users\Administrator\private_monitor")
PENDING_FILE = PLUGIN_ROOT / "pending_send.json"
RESULT_FILE = PLUGIN_ROOT / "send_result.json"

def send_text(title: str, text: str, *, mention: str='', contact: str=''):
    text = str(text or '').strip()
    if not text:
        return {'sent': False, 'reason': 'empty_reply'}
    
    target = str(contact or title or '无言').strip()
    
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
        
    subprocess.run(['schtasks', '/run', '/tn', 'Xinghui-Scan-Win'], capture_output=True, timeout=10)
    
    deadline = time.time() + 8.0
    res_data = None
    while time.time() < deadline:
        if RESULT_FILE.exists():
            try:
                with open(RESULT_FILE, 'r', encoding='utf-8-sig') as f:
                    res_data = json.load(f)
                break
            except Exception:
                pass
        time.sleep(0.3)
        
    if res_data and res_data.get('status') == 'success':
        return {'sent': True, 'target': target, 'contact': target, 'res': res_data}
    else:
        err = res_data.get('error') if res_data else 'timeout_waiting_for_task'
        raise RuntimeError(f'Physical send failed: {err}')

