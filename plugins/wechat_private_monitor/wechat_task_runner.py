# -*- coding: utf-8 -*-
import sys, os, time, json, ctypes
from ctypes import wintypes
import win32clipboard, win32con

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

base_dir = r"C:\Users\Administrator\private_monitor"
pending_file = os.path.join(base_dir, "pending_send.json")
result_file = os.path.join(base_dir, "send_result.json")

def set_clip(text):
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            return True
        except Exception:
            time.sleep(0.05)
    return False

def get_clip():
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            else:
                data = ""
            win32clipboard.CloseClipboard()
            return data
        except Exception:
            time.sleep(0.05)
    return ""

def press_keys(*vks):
    KEYEVENTF_KEYUP = 0x0002
    for vk in vks:
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.02)
    time.sleep(0.05)
    for vk in reversed(vks):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)

def check_input_empty():
    VK_CONTROL = 0x11
    VK_A = 0x41
    VK_C = 0x43
    set_clip("")
    time.sleep(0.1)
    press_keys(VK_CONTROL, VK_A)
    time.sleep(0.05)
    press_keys(VK_CONTROL, VK_C)
    time.sleep(0.1)
    clip = get_clip()
    return (len(clip.strip()) == 0), clip

def run():
    if not os.path.exists(pending_file):
        print("No pending file.")
        return

    try:
        with open(pending_file, "r", encoding="utf-8-sig") as f:
            task = json.load(f)
    except Exception as e:
        with open(result_file, "w", encoding="utf-8-sig") as f:
            json.dump({"status": "error", "error": f"read_error: {e}"}, f)
        return

    target_title = task.get("title") or task.get("target") or "无言"
    text = task.get("text", "")
    mention_nickname = task.get("mention_nickname") or task.get("mention")

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
    actual_title = ""
    for hwnd, title in wins:
        if title.strip() == target_title.strip():
            target_hwnd = hwnd
            actual_title = title
            break
    if not target_hwnd:
        for hwnd, title in wins:
            if target_title.strip() in title:
                target_hwnd = hwnd
                actual_title = title
                break

    if not target_hwnd:
        with open(result_file, "w", encoding="utf-8-sig") as f:
            json.dump({"status": "error", "error": f"window_not_found: {target_title}"}, f, ensure_ascii=False)
        return

    # Focus window
    user32.ShowWindow(target_hwnd, 9)
    user32.ShowWindow(target_hwnd, 5)

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

    # Click input area (bottom - 70)
    rect = wintypes.RECT()
    user32.GetWindowRect(target_hwnd, ctypes.byref(rect))
    click_x = (rect.left + rect.right) // 2
    click_y = rect.bottom - 70

    user32.SetCursorPos(click_x, click_y)
    time.sleep(0.05)
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.2)

    VK_CONTROL = 0x11
    VK_MENU = 0x12  # Alt
    VK_V = 0x56
    VK_RETURN = 0x0D
    VK_SHIFT = 0x10
    VK_2 = 0x32
    VK_S = 0x53
    VK_BACK = 0x08

    # Select all and delete whatever was leftover before pasting
    press_keys(VK_CONTROL, 0x41) # Ctrl+A
    time.sleep(0.05)
    press_keys(VK_BACK) # Backspace
    time.sleep(0.1)

    if mention_nickname:
        press_keys(VK_SHIFT, VK_2)
        time.sleep(0.6)
        set_clip(mention_nickname)
        press_keys(VK_CONTROL, VK_V)
        time.sleep(0.5)
        press_keys(VK_RETURN)
        time.sleep(0.5)

    set_clip(text)
    time.sleep(0.2)
    press_keys(VK_CONTROL, VK_V)
    time.sleep(0.4)

    # Attempt 1: Enter
    press_keys(VK_RETURN)
    time.sleep(0.5)
    empty, clip = check_input_empty()
    method_used = "Enter"

    # Attempt 2: If Enter resulted in newline or didn't send, try Alt+S (Send)
    if not empty:
        press_keys(VK_MENU, VK_S)
        time.sleep(0.5)
        empty, clip = check_input_empty()
        method_used = "Alt+S"

    # Attempt 3: If still not empty, try Ctrl+Enter
    if not empty:
        press_keys(VK_CONTROL, VK_RETURN)
        time.sleep(0.5)
        empty, clip = check_input_empty()
        method_used = "Ctrl+Enter"

    # Attempt 4: Click Send button
    if not empty:
        btn_x = rect.right - 55
        btn_y = rect.bottom - 25
        user32.SetCursorPos(btn_x, btn_y)
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.5)
        empty, clip = check_input_empty()
        method_used = "ClickSendButton"

    res = {
        "status": "success" if empty else "failed",
        "target": target_title,
        "actual_title": actual_title,
        "method_used": method_used,
        "remaining": clip
    }
    with open(result_file, "w", encoding="utf-8-sig") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    try:
        os.remove(pending_file)
    except:
        pass

if __name__ == "__main__":
    run()

