# -*- coding: utf-8 -*-
"""WeChat image vision bridge.

Pipeline:
  1. detect trigger (peek sees [image] tokens OR user referenced an image)
  2. rate limit per wechat pid
  3. memory scan via procmem_image_scan.scan_images
  4. dedup by sha + vision describe (24h cache)
  5. return a text block to be appended to prompt
"""

from __future__ import annotations
import hashlib
import json
import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Optional, List

_THIS_DIR = Path(__file__).resolve().parent
_CACHE_ROOT = _THIS_DIR / 'runtime' / 'img_vision_cache'
_IMG_DIR = _CACHE_ROOT / 'imgs'
_DESC_DIR = _CACHE_ROOT / 'desc'
_STATE_FILE = _CACHE_ROOT / 'state.json'

YAMA_TOGGLE = '/usr/local/bin/yama_toggle.sh'

# Ensure L3 scanner importable
_L3_DIR = str(Path(os.environ.get('GA_ROOT')
                   or Path(__file__).resolve().parents[2])
              / 'memory' / 'L3_shared')
if _L3_DIR not in sys.path:
    sys.path.insert(0, _L3_DIR)

DEFAULT_CFG = {
    'enable': True,
    'scan_cooldown_sec': 60,
    'max_images_per_scan': 200,
    'min_image_size': 5000,
    'max_image_size': 20 * 1024 * 1024,
    'describe_cache_ttl_sec': 24 * 3600,
    'top_recent': 6,
    'ocr_enable': True,
    'ocr_min_chars': 4,
    'ocr_max_chars': 800,
    'vision_skip_when_ocr_chars': 260,
    'vision_model_prompt': (
        '请用一两句中文客观描述这张图（聊天截图/表情/照片/同人图等），'
        '若是文字截图请摘录关键文字。'
    ),
}


def _cfg():
    p = _THIS_DIR / 'clawbot_image_vision.json'
    cfg = dict(DEFAULT_CFG)
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text('utf-8')))
        except Exception:
            pass
    return cfg


def _ensure_dirs():
    _IMG_DIR.mkdir(parents=True, exist_ok=True)
    _DESC_DIR.mkdir(parents=True, exist_ok=True)


def _load_state():
    try:
        return json.loads(_STATE_FILE.read_text('utf-8'))
    except Exception:
        return {}


def _save_state(st):
    try:
        _STATE_FILE.write_text(json.dumps(st, ensure_ascii=False), 'utf-8')
    except Exception:
        pass


def _find_wechat_pid() -> Optional[int]:
    try:
        out = subprocess.check_output(
            ['pgrep', '-nf', '^/usr/bin/wechat$'],
            stderr=subprocess.DEVNULL, text=True).strip()
        if out:
            return int(out.splitlines()[0])
    except Exception:
        pass
    try:
        out = subprocess.check_output(['ps', '-eo', 'pid,comm,args'], text=True)
        for ln in out.splitlines():
            parts = ln.split(None, 2)
            if len(parts) >= 3 and parts[1] == 'wechat' and '/usr/bin/wechat' in parts[2]:
                return int(parts[0])
    except Exception:
        pass
    return None


def _yama_open():
    try:
        subprocess.run(['sudo', '-n', YAMA_TOGGLE, 'open'],
                       check=False, capture_output=True, timeout=5)
    except Exception:
        pass


def _yama_close():
    try:
        subprocess.run(['sudo', '-n', YAMA_TOGGLE, 'close'],
                       check=False, capture_output=True, timeout=5)
    except Exception:
        pass


def _sha8(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def _image_ext(path: Path, data: bytes = b'') -> str:
    name = path.name.lower()
    if name.endswith('.png') or data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    return 'jpg'


def _cached_images() -> List[Path]:
    files = list(_IMG_DIR.glob('*.jpg')) + list(_IMG_DIR.glob('*.png'))
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _recent_seen_images(st: Optional[dict] = None, max_age_sec: float = 1800) -> List[Path]:
    """Return images explicitly observed by recent scans, not by file mtime.

    File mtime is unsafe here: one broad memory scan can touch/promote many old
    WeChat buffers and make historical images look current.  `img_seen_at` is
    only updated for brand-new images discovered by this bridge.
    """
    if st is None:
        st = _load_state()
    now = time.time()
    seen = st.get('img_seen_at') or {}
    pairs = []
    for name, ts in list(seen.items()):
        try:
            tsf = float(ts)
        except Exception:
            continue
        if now - tsf > max_age_sec:
            continue
        p = _IMG_DIR / name
        if p.exists():
            pairs.append((tsf, p))
    pairs.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in pairs]


def _is_valid_image(path: Path) -> bool:
    try:
        from PIL import Image  # noqa: WPS433
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


def _clean_ocr_text(txt: str, max_chars: int = 800) -> str:
    txt = (txt or '').strip()
    lines = []
    seen = set()
    for ln in txt.splitlines():
        ln = ' '.join(ln.strip().split())
        if not ln or ln in seen:
            continue
        seen.add(ln)
        lines.append(ln)
    out = '\n'.join(lines).strip()
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + '…'
    return out


def _ocr_one(img_path: Path, cfg) -> str:
    if cfg.get('ocr_enable') is False:
        return ''
    try:
        # rapidocr is installed in the shared OCR venv; service python may not see it.
        import glob
        for sp in glob.glob(os.environ.get("RAPIDOCR_SITE_PACKAGES_GLOB", str(Path(__file__).resolve().parents[2] / ".venv_rapidocr/lib/python*/site-packages"))):
            if sp not in sys.path:
                sys.path.insert(0, sp)
        from ocr_utils import ocr_image  # noqa: WPS433
    except Exception as e:
        print(f'[img_vision] import ocr_utils err: {e}', file=sys.stderr)
        return ''
    try:
        r = ocr_image(str(img_path), enhance=False, engine='rapid')
        return _clean_ocr_text(r.get('text') or '', int(cfg.get('ocr_max_chars') or 800))
    except Exception as e:
        print(f'[img_vision] ocr err {img_path.name}: {e}', file=sys.stderr)
        return ''


def _format_desc(ocr_text: str = '', vision_desc: str = '') -> str:
    parts = []
    if ocr_text:
        parts.append('OCR文字摘录：' + ocr_text.replace('\n', ' / '))
    if vision_desc:
        parts.append('画面描述：' + vision_desc)
    return '；'.join(parts).strip()


def _describe_one(img_path: Path, vision_prompt: str) -> Optional[str]:
    try:
        from vision_api import ask_vision  # noqa: WPS433
    except Exception as e:
        print(f'[img_vision] import vision_api err: {e}', file=sys.stderr)
        return None
    try:
        r = ask_vision(str(img_path), vision_prompt)
        if isinstance(r, dict):
            txt = (r.get('text') or r.get('desc') or '').strip()
            return txt or None
        if isinstance(r, str):
            return r.strip() or None
    except Exception as e:
        print(f'[img_vision] vision err {img_path.name}: {e}', file=sys.stderr)
    return None


def _gather_recent_images(cfg) -> List[Path]:
    pid = _find_wechat_pid()
    if not pid:
        return []
    now = time.time()
    st = _load_state()
    last_scan = float(st.get('last_scan_ts') or 0)
    if now - last_scan < float(cfg.get('scan_cooldown_sec') or 60):
        return _recent_seen_images(st, float(cfg.get('recent_seen_ttl_sec') or 1800))

    try:
        from procmem_image_scan import scan_images
    except Exception as e:
        print(f'[img_vision] import scan err: {e}', file=sys.stderr)
        return _recent_seen_images(st, float(cfg.get('recent_seen_ttl_sec') or 1800))

    tmpdir = _CACHE_ROOT / '_scan_tmp'
    if tmpdir.exists():
        for f in tmpdir.iterdir():
            try:
                f.unlink()
            except Exception:
                pass
    tmpdir.mkdir(parents=True, exist_ok=True)

    _yama_open()
    try:
        try:
            r = scan_images(pid, out_dir=str(tmpdir),
                            min_size=int(cfg['min_image_size']),
                            max_size=int(cfg['max_image_size']),
                            max_img=int(cfg['max_images_per_scan']))
        except Exception as e:
            print(f'[img_vision] scan err: {e}', file=sys.stderr)
            r = {'files': []}
    finally:
        _yama_close()

    scan_hits = []
    scan_names = []
    for f in (r.get('files') or []):
        try:
            src = Path(f)
            b = src.read_bytes()
            h = _sha8(b)
            ext = _image_ext(src, b)
            if ext not in ('jpg', 'png') or not _is_valid_image(src):
                continue
            dst = _IMG_DIR / f'{h}.{ext}'
            if not dst.exists():
                dst.write_bytes(b)
                # Preserve mtime only as a weak local-cache hint.  Prompt
                # recency is tracked by img_seen_at/last_scan_names below.
                try:
                    os.utime(dst, None)
                except Exception:
                    pass
            scan_hits.append(dst)
            scan_names.append(dst.name)
        except Exception:
            pass

    for f in tmpdir.iterdir():
        try:
            f.unlink()
        except Exception:
            pass

    st['last_scan_ts'] = now
    st['last_scan_pid'] = pid
    st['last_scan_found'] = int((r.get('jpg') or 0) + (r.get('png') or 0))
    st['last_scan_cached'] = len(scan_hits)

    prev_pid = st.get('scan_baseline_pid')
    prev_names = set(st.get('last_scan_names') or [])
    cur_names = set(scan_names)
    if prev_pid == pid and prev_names:
        new_names = cur_names - prev_names
    else:
        # First scan after restart/PID change is only a baseline: WeChat memory
        # may already contain many historical image buffers.
        new_names = set()
    st['last_scan_new'] = len(new_names)
    st['last_scan_reused'] = len(cur_names - new_names)
    st['last_scan_names'] = sorted(cur_names)[-500:]
    st['scan_baseline_pid'] = pid

    seen_at = st.get('img_seen_at') or {}
    # Only images newly appearing in the live process-memory set are treated as
    # current chat images.  Reused hits from broad scans can be arbitrarily old.
    for name in new_names:
        if (_IMG_DIR / name).exists():
            seen_at[name] = now
    cutoff = now - float(cfg.get('recent_seen_ttl_sec') or 1800)
    seen_at = {
        name: ts for name, ts in seen_at.items()
        if isinstance(name, str) and float(ts or 0) >= cutoff and (_IMG_DIR / name).exists()
    }
    st['img_seen_at'] = seen_at
    _save_state(st)
    recent = _recent_seen_images(st, float(cfg.get('recent_seen_ttl_sec') or 1800))
    if recent:
        return recent
    # Deliberately do not fall back to mtime-sorted cache here: mtime may have
    # been polluted by earlier scans, causing old/history images to be injected.
    return []


def _desc_for(img: Path, cfg) -> Optional[str]:
    h = img.stem
    dfile = _DESC_DIR / f'{h}.json'
    ttl = float(cfg.get('describe_cache_ttl_sec') or 86400)
    if dfile.exists():
        try:
            obj = json.loads(dfile.read_text('utf-8'))
            if time.time() - float(obj.get('ts') or 0) < ttl and obj.get('desc'):
                return obj['desc']
        except Exception:
            pass
    if not _is_valid_image(img):
        return None

    ocr_text = _ocr_one(img, cfg)
    ocr_enough = len(ocr_text) >= int(cfg.get('vision_skip_when_ocr_chars') or 260)
    vision_desc = ''
    if not ocr_enough:
        vision_desc = _describe_one(
            img,
            cfg.get('vision_model_prompt') or DEFAULT_CFG['vision_model_prompt']) or ''
    desc = _format_desc(ocr_text if len(ocr_text) >= int(cfg.get('ocr_min_chars') or 4) else '',
                        vision_desc)
    if desc:
        try:
            dfile.write_text(
                json.dumps({
                    'desc': desc,
                    'ocr_text': ocr_text,
                    'vision_desc': vision_desc,
                    'ocr_engine': 'rapidocr',
                    'vision_model': 'gemini-3-flash-preview' if vision_desc else '',
                    'ts': time.time(),
                    'kind': img.suffix.lstrip('.').lower(),
                }, ensure_ascii=False),
                'utf-8')
        except Exception:
            pass
    return desc


def build_image_vision_block(reason: str = 'peek') -> str:
    """Main entry. scan memory, describe recent imgs, return a prompt block.

    reason: 'peek' (group side-channel [image] trigger) or 'reply' (private ref).
    Returns empty string when nothing to inject.

    Important: memory scan may refresh many already-known cached images at once.
    Therefore we first pre-describe a bounded number of images that do not yet
    have a desc cache, then render the newest described images.  Otherwise the
    topN can be permanently occupied by old described images and newly cached
    pictures never get a chance to run OCR/vision.
    """
    cfg = _cfg()
    if cfg.get('enable') is False:
        return ''
    _ensure_dirs()
    imgs = _gather_recent_images(cfg)
    if not imgs:
        return ''
    topN = int(cfg.get('top_recent') or 6)
    prewarmN = int(cfg.get('prewarm_unseen_per_call') or max(topN, 6))

    # Prefer spending OCR/vision budget on candidates without a fresh desc cache.
    # Keep it bounded so one busy group burst will be processed over several
    # triggers instead of blocking the reply loop for too long.
    prewarmed = 0
    for p in imgs:
        dfile = _DESC_DIR / (p.stem + '.json')
        has_fresh_desc = False
        if dfile.exists():
            try:
                obj = json.loads(dfile.read_text('utf-8'))
                has_fresh_desc = (
                    time.time() - float(obj.get('ts') or 0) <
                    float(cfg.get('describe_cache_ttl_sec') or 24 * 3600)
                    and bool(obj.get('desc'))
                )
            except Exception:
                has_fresh_desc = False
        if has_fresh_desc:
            continue
        _desc_for(p, cfg)
        prewarmed += 1
        if prewarmed >= prewarmN:
            break

    # Render newest images that now have descriptions, not just the first topN
    # raw scan hits.  This lets just-prewarmed images enter the injected block.
    desc_items = []
    for p in imgs:
        dfile = _DESC_DIR / (p.stem + '.json')
        if not dfile.exists():
            continue
        try:
            obj = json.loads(dfile.read_text('utf-8'))
            desc = obj.get('desc') or ''
            ts = float(obj.get('ts') or dfile.stat().st_mtime)
        except Exception:
            continue
        if desc:
            desc_items.append((ts, desc))
    desc_items.sort(key=lambda x: x[0], reverse=True)
    descs = [d for _, d in desc_items[:topN]]
    if not descs:
        return ''
    if reason == 'peek':
        header = '\n【最近群内流传的图片（OCR文字+vision描述，按最新到旧）】\n'
    else:
        header = '\n【近期微信缓存图片（OCR文字+vision描述）】\n'
    body = '\n'.join(f'- {d}' for d in descs)
    footer = '\n（注：图片与上方 [图片] 标记按时间顺序对应，无法逐条精确配对）'
    return header + body + footer


if __name__ == '__main__':
    print(build_image_vision_block('peek'))
