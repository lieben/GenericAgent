# -*- coding: utf-8 -*-
import sys, os, time, json, ctypes
from ctypes import wintypes
import subprocess

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

base_dir = r"C:\Users\Administrator\private_monitor"
pending_file = os.path.join(base_dir, "pending_send.json")
result_file = os.path.join(base_dir, "send_result.json")

def log(msg):
    try:
        with open(os.path.join(base_dir, "runner.log"), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

def write_result(data):
    try:
        tmp = result_file + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if os.path.exists(result_file):
            os.remove(result_file)
        os.rename(tmp, result_file)
    except Exception as e:
        log(f"Write result error: {e}")

if not os.path.exists(pending_file):
    sys.exit(0)

try:
    with open(pending_file, "r", encoding="utf-8-sig") as f:
        task = json.load(f)
except Exception as e:
    log(f"Read pending error: {e}")
    sys.exit(1)

target = task.get("target") or task.get("title", "无言")
message = task.get("message") or task.get("text", "")

if not message:
    write_result({"status": "failed", "error": "empty_message"})
    sys.exit(0)

log(f"Processing send task: target={target}, msg_len={len(message)}")

wins = []
def callback(hwnd, extra):
    if user32.IsWindowVisible(hwnd):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            wins.append((hwnd, buff.value))
    return True

CMPFUNC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows(CMPFUNC(callback), 0)

target_hwnd = None
for hwnd, title in wins:
    if title.strip() == target.strip():
        target_hwnd = hwnd
        break

if not target_hwnd:
    for hwnd, title in wins:
        if target.strip() in title:
            target_hwnd = hwnd
            break

if not target_hwnd:
    log(f"Window not found: {target}")
    write_result({"status": "failed", "error": f"window_not_found: {target}"})
    sys.exit(0)

# Bring window to front
user32.ShowWindow(target_hwnd, 9) # SW_RESTORE
user32.ShowWindow(target_hwnd, 5) # SW_SHOW

fore_hwnd = user32.GetForegroundWindow()
fore_thread = user32.GetWindowThreadProcessId(fore_hwnd, None)
target_thread = user32.GetWindowThreadProcessId(target_hwnd, None)
cur_thread = kernel32.GetCurrentThreadId()

user32.AttachThreadInput(cur_thread, target_thread, True)
user32.AttachThreadInput(fore_thread, target_thread, True)

user32.BringWindowToTop(target_hwnd)
user32.SetForegroundWindow(target_hwnd)
user32.SetFocus(target_hwnd)

user32.AttachThreadInput(cur_thread, target_thread, False)
user32.AttachThreadInput(fore_thread, target_thread, False)

time.sleep(0.3)

# Write message to temp utf-8 file and use PowerShell Get-Content -Raw | Set-Clipboard
temp_txt = os.path.join(base_dir, "temp_msg.txt")
with open(temp_txt, "w", encoding="utf-8") as f:
    f.write(message)

# Set-Clipboard safely without shell escaping corruption
ps_cmd = f'[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-Content -LiteralPath "{temp_txt}" -Raw -Encoding UTF8 | Set-Clipboard'
p = subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_cmd])
p.wait()

time.sleep(0.2)

# Send Ctrl+V then Enter via keybd_event
VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D
KEYEVENTF_KEYUP = 0x0002

user32.keybd_event(VK_CONTROL, 0, 0, 0)
user32.keybd_event(VK_V, 0, 0, 0)
user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

time.sleep(0.3)

user32.keybd_event(VK_RETURN, 0, 0, 0)
user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)

log("Send succeeded!")
write_result({"status": "success", "target": target, "time": time.time()})

try:
    if os.path.exists(pending_file):
        os.remove(pending_file)
except Exception:
    pass
