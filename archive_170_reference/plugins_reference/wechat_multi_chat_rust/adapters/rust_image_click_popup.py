#!/usr/bin/env python3
# WeChat window-crop OCR+Vision adapter (safe no-click flow).
import json, os, sys, time, subprocess
from pathlib import Path

# --- stdout isolation ---
try:
    _REAL = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = os.fdopen(2, "w", encoding="utf-8", closefd=False)
except Exception:
    _REAL = None

def emit(payload):
    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    if _REAL is not None:
        try:
            os.write(_REAL, data); return
        except Exception:
            pass
    sys.__stdout__.buffer.write(data); sys.__stdout__.flush()

GA_ROOT = Path(os.environ.get("GA_ROOT", Path(__file__).resolve().parents[3]))
L3 = str(Path(os.environ.get("GA_L3_SHARED", GA_ROOT / "memory" / "L3_shared")))
if L3 not in sys.path:
    sys.path.insert(0, L3)

# rapidocr_onnxruntime should be installed in the active agent venv; do not depend on old GenericAgent venvs.

DEFAULT_DIR = "/tmp/wechat_imgs"
DEFAULT_PROMPT = "请详细描述这张图片的核心内容(人物/物品/动作/文字/场景/表情)。如果是截图或聊天,请把可读文字逐条转写。限300字内。"
DEFAULT_VISION_MODEL = "gemini-3-flash-preview"

from x11_display import x_env

def run(cmd, timeout=8):
    env = x_env()
    try:
        return subprocess.run(cmd, env=env, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except Exception as e:
        sys.stderr.write("[run] " + str(cmd[:3]) + " " + str(e) + "\n")
        return None

def activate_wid(wid):
    run(["wmctrl", "-ia", wid], timeout=3)
    time.sleep(0.4)

def grab_window(wid, out_path):
    r = run(["import", "-window", wid, out_path], timeout=8)
    return r is not None and os.path.exists(out_path) and os.path.getsize(out_path) > 200

def crop_window_block(win_png, cand, win_w, win_h, out_path):
    """Crop the image block directly from the already captured WeChat window.

    This intentionally does NOT click anything and never captures the root
    desktop.  The crop is approximate around the vision-returned center; for
    quoted images it includes the grey quote card text + thumbnail.
    """
    try:
        from PIL import Image
    except Exception as e:
        sys.stderr.write("[crop import] " + str(e) + "\n")
        return False
    try:
        cx = int(cand.get("cx")); cy = int(cand.get("cy"))
        typ = (cand.get("type") or "normal").lower()
        if typ == "quote":
            half_w, half_h = 145, 70
        else:
            half_w, half_h = 150, 150
        left = max(0, cx - half_w)
        top = max(0, cy - half_h)
        right = min(int(win_w), cx + half_w)
        bottom = min(int(win_h), cy + half_h)
        if right - left < 20 or bottom - top < 20:
            return False
        im = Image.open(win_png)
        im.crop((left, top, right, bottom)).save(out_path)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 200
    except Exception as e:
        sys.stderr.write("[crop] " + str(e) + "\n")
        return False

def _configure_vision_model(vision_model=None):
    """Local to this adapter process: switch shared vision_api to the requested config.

    We intentionally do not edit global vision_api defaults, so clawbot/other callers
    keep their own default behavior. native_oai_config is verified to point at
    gemini-3-flash-preview in this environment.
    """
    import vision_api
    if vision_model in ("gemini-3-flash-preview"):
        vision_api.OPENAI_CONFIG_KEY = "native_oai_config"
    return vision_api.ask_vision

def do_ocr(img_path):
    try:
        from ocr_utils import ocr_image
        res = ocr_image(img_path)
        if isinstance(res, dict):
            txt = res.get("text") or ""
            return txt.strip()
        if isinstance(res, str):
            return res.strip()
    except Exception as e:
        sys.stderr.write("[ocr] " + str(e) + "\n")
    return ""

def do_vision(img_path, prompt, vision_model=None):
    try:
        ask_vision = _configure_vision_model(vision_model)
        r = ask_vision(img_path, prompt, timeout=45)
        if r and not r.startswith("Error"):
            return r.strip()
    except Exception as e:
        sys.stderr.write("[vision] " + str(e) + "\n")
    return ""

def find_image_blocks_via_vision(img_path, win_w, win_h, vision_model=None):
    try:
        ask_vision = _configure_vision_model(vision_model)
    except Exception as e:
        sys.stderr.write("[vision import] " + str(e) + "\n")
        return []
    prompt = (
        "这是一张微信聊天窗口截图(宽 "
        + str(win_w) + " 高 " + str(win_h) + ", 左上 0,0).\n"
        "请找出窗口内所有可见的图片消息/引用图片消息,分两类:\n"
        "  A. 普通图片(消息气泡里直接就是图)\n"
        "  B. 引用消息(灰色细框小气泡,内含 'XX: [图片]' 文字 + 一个小缩略图)\n"
        "不要包含:表情包贴纸/聊天背景图/头像。\n"
        "输出 JSON 数组,每项含:type('normal'或'quote'),cx,cy,note.\n"
        "  - normal 类型: cx,cy 取图片本身可见区域的几何中心\n"
        "  - quote 类型: cx,cy 取整个灰色引用框的几何中心(覆盖文字+小图整体,不要只对准小缩略图)\n"
        "坐标是窗口内相对坐标。若有 quote,请放在数组最前面(最近引用优先).\n"
        "示例:[{\"type\":\"quote\",\"cx\":140,\"cy\":860,\"note\":\"底部引用框中心\"}]\n"
        "只输出JSON数组,不要markdown,不要解释。"
    )
    try:
        raw = ask_vision(img_path, prompt, timeout=45)
    except Exception as e:
        sys.stderr.write("[vision] " + str(e) + "\n"); return []
    if not raw or raw.startswith("Error"):
        sys.stderr.write("[vision] bad: " + (raw[:200] if raw else "empty") + "\n"); return []
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if "\n" in s:
            s = s.split("\n", 1)[1]
    l = s.find("["); r = s.rfind("]")
    if l < 0 or r < 0 or r <= l:
        sys.stderr.write("[vision] no array: " + s[:200] + "\n"); return []
    try:
        arr = json.loads(s[l:r+1])
    except Exception as e:
        sys.stderr.write("[vision parse] " + str(e) + "\n"); return []
    out = []
    for it in arr:
        try:
            t = (it.get("type") or "normal").lower()
            cx = int(it.get("cx")); cy = int(it.get("cy"))
            if 0 <= cx <= win_w and 0 <= cy <= win_h:
                out.append({"type": t, "cx": cx, "cy": cy, "note": it.get("note", "")})
        except Exception:
            continue
    out.sort(key=lambda x: 0 if x["type"] == "quote" else 1)
    return out

def process_one(idx, cand, win_png, win_geom, sdir, vprompt, do_vision_flag, vision_model=None):
    wx, wy, ww, wh = win_geom[0], win_geom[1], win_geom[2], win_geom[3]
    xs = wx + int(cand.get("cx", 0)); ys = wy + int(cand.get("cy", 0))
    rec = {
        "idx": idx, "type": cand.get("type"), "note": cand.get("note", ""),
        "x_screen": xs, "y_screen": ys,
        # Keep field name compatible with the caller; here opened means
        # successfully cropped from the WeChat window screenshot, not clicked.
        "opened": False, "popup_png": "", "crop_png": "",
        "ocr_text": "", "vision_text": "", "error": "",
    }
    ts = int(time.time() * 1000)
    out = os.path.join(sdir, "crop_{}_{}.png".format(ts, idx))
    if not crop_window_block(win_png, cand, ww, wh, out):
        rec["error"] = "crop_failed"
        return rec
    rec["opened"] = True
    rec["popup_png"] = out
    rec["crop_png"] = out
    rec["ocr_text"] = do_ocr(out)
    if do_vision_flag:
        rec["vision_text"] = do_vision(out, vprompt, vision_model=vision_model)
    return rec

def build_summary(clicks):
    parts = []
    for c in clicks:
        if not c.get("opened"):
            continue
        ocr = (c.get("ocr_text") or "").strip()
        vis = (c.get("vision_text") or "").strip()
        if not ocr and not vis:
            continue
        tag = "引用图" if c.get("type") == "quote" else "图片"
        lines = ["[{}#{}]".format(tag, c["idx"] + 1)]
        if ocr:
            o = ocr if len(ocr) <= 400 else ocr[:400] + "..."
            lines.append("文字识别: " + o)
        if vis:
            lines.append("画面观察: " + vis)
        parts.append("\n".join(lines))
    return "\n\n".join(parts)

def main():
    t0 = time.time()
    try:
        raw = sys.stdin.read()
        cfg = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        emit({"ok": False, "error": "bad stdin: " + str(e)}); return
    wid = cfg.get("wid"); win_geom = cfg.get("win_geom")
    if not wid or not win_geom or len(win_geom) != 4:
        emit({"ok": False, "error": "missing wid/win_geom"}); return
    sdir = cfg.get("screenshots_dir") or DEFAULT_DIR
    Path(sdir).mkdir(parents=True, exist_ok=True)
    vprompt = cfg.get("vision_prompt") or DEFAULT_PROMPT
    vision_model = cfg.get("vision_model") or DEFAULT_VISION_MODEL
    # 默认只 OCR,不跑 vision(慢且贵);需要时由上层按 popup_png 路径另调 ask_vision
    do_vision_flag = bool(cfg.get("do_vision", False))
    do_summary_flag = bool(cfg.get("do_summary", False))
    activate_wid(wid)
    win_png = os.path.join(sdir, "win_{}.png".format(int(time.time() * 1000)))
    if not grab_window(wid, win_png):
        emit({"ok": False, "error": "grab_window_failed"}); return
    cands = find_image_blocks_via_vision(win_png, win_geom[2], win_geom[3], vision_model=vision_model)
    if not cands:
        emit({"ok": True, "win_png": win_png, "clicks": [], "summary": "",
              "note": "no_image_block", "elapsed_ms": int((time.time() - t0) * 1000)})
        return
    clicks = []
    for i, c in enumerate(cands):
        try:
            clicks.append(process_one(i, c, win_png, win_geom, sdir, vprompt, do_vision_flag, vision_model=vision_model))
        except Exception as e:
            clicks.append({"idx": i, "type": c.get("type"), "error": "exc: " + str(e)})
    emit({"ok": True, "win_png": win_png, "clicks": clicks,
          "summary": build_summary(clicks) if do_summary_flag else "",
          "elapsed_ms": int((time.time() - t0) * 1000)})

if __name__ == "__main__":
    main()
