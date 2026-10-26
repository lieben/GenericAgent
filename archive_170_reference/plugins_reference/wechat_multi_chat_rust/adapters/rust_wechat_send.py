#!/usr/bin/env python3
"""Physical WeChat sender adapter for Rust multi-chat.

It performs conservative foreground targeting and optional post-send echo check
through the same DB tail adapter contract data carried in target.live_db.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tempfile
import shutil
from pathlib import Path

from x11_display import x_env

ROOT = Path(__file__).resolve().parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_DIR = Path(__file__).resolve().parent
SUPPORTED_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
EMOJI_TOKEN_RE = re.compile(r'^\s*\[\[EMOJI:([^\]：|]+)\]\]\s*$')
EMOJI_TOKEN_FIND_RE = re.compile(r'(\[\[EMOJI:[^\]：|]+\]\])')
CONTEXT_POLLUTION_RE = re.compile(
    r'(?:\[(?:/?(?:Context|Contex|RecentContext|earlier_context|history|key_info|WORKING MEMORY))\]|'
    r'###\s*\[WORKING MEMORY\]|<[/]?(?:earlier_context|history|key_info)>|'
    r'我这边暂时没能完成实时查证|不能把不确定的信息当成结果|'
    r'你可以把链接或截图发我|按你给的来源一起看|凭已有记忆判断|我也会明确标出来|'
    r'反幻觉兜底|无法查证|没能完成查证)',
    re.IGNORECASE,
)


def strip_context_pollution(text: str) -> str:
    raw = str(text or '')
    if not raw:
        return ''
    # If the injected working-memory block is appended to a visible reply, keep
    # only the visible prefix. If it is the whole candidate bubble, this yields
    # empty text and blocks the send.
    raw = re.split(r'(?is)(?:^|\n)\s*###\s*\[WORKING MEMORY\].*', raw, maxsplit=1)[0]
    raw = re.sub(r'(?is)\[Contex(?:t)?[^\n\[]*?(?:(?:\.\.\.|…)[)）]?|\)|）)', ' ', raw)
    raw = re.sub(r'(?is)\[(?:/?(?:Context|Contex|RecentContext|earlier_context|history|key_info|WORKING MEMORY))\][^\n]*', ' ', raw)
    raw = re.sub(r'(?is)<(?:earlier_context|history|key_info)>.*?</(?:earlier_context|history|key_info)>', ' ', raw)
    raw = re.sub(r'(?is)<[/]?(?:earlier_context|history|key_info)>[^\n]*', ' ', raw)
    raw = re.sub(
        r'(?is)我这边暂时没能完成实时查证[^。！？!?\n]*(?:[。！？!?]|$)'
        r'(?:\s*(?:你可以把链接或截图发我|如果只是要我凭已有记忆判断)[^。！？!?\n]*(?:[。！？!?]|$))*',
        ' ',
        raw,
    )
    kept = []
    for line in raw.splitlines():
        if CONTEXT_POLLUTION_RE.search(line.strip()):
            continue
        kept.append(line)
    return '\n'.join(kept).strip()


def run(cmd, **kw):
    env = x_env()
    extra = kw.pop('env', None)
    if extra:
        env.update(extra)
    return subprocess.run(cmd, text=True, capture_output=True, timeout=kw.pop('timeout', 5), env=env, **kw)

def fail(msg):
    print(json.dumps({'sent': False, 'adapter_error': msg}, ensure_ascii=False))
    raise SystemExit(0)

def find_window(title):
    last_err = None
    wins = []
    # X11/XRDP can occasionally stall for a few seconds right after session
    # recovery or window focus changes.  A single timeout used to raise a
    # Python traceback, which made the Rust loop record target_error after LLM
    # generation and skip the actual send.  Keep this adapter conservative:
    # retry the read a few times, then return a normal adapter_error detail.
    for attempt in range(3):
        try:
            r = run(['wmctrl', '-lx'], timeout=8)
            if r.returncode != 0:
                last_err = f'wmctrl -lx rc={r.returncode} stderr={r.stderr.strip()!r}'
            else:
                wins = r.stdout.splitlines()
                break
        except subprocess.TimeoutExpired as e:
            last_err = f'wmctrl -lx timeout after {e.timeout}s (attempt {attempt + 1}/3)'
        if attempt < 2:
            time.sleep(0.4)
    if not wins:
        return None, last_err or 'wmctrl -lx returned no windows'
    matches = []
    for line in wins:
        if title in line:
            parts = line.split(None, 4)
            if parts:
                matches.append((parts[0], line))
    if len(matches) == 1:
        return matches[0][0], matches[0][1]
    if not matches:
        return None, None
    return None, f'ambiguous target window {title!r}: ' + ' | '.join(line for _, line in matches)

def find_unique_wechat_window():
    r = run(['wmctrl', '-lG'], timeout=8)
    if r.returncode != 0:
        return None, f'wmctrl -lG rc={r.returncode} stderr={r.stderr.strip()!r}'
    matches = []
    for line in r.stdout.splitlines():
        # Current Ubuntu xwechat often keeps the X11 title as plain "微信" even
        # when a group chat is open; use the main-size WeChat window only.
        if line.rstrip().endswith(' 微信'):
            parts = line.split(None, 7)
            if len(parts) >= 8:
                try:
                    w, h = int(parts[4]), int(parts[5])
                except Exception:
                    w = h = 0
                if w >= 650 and h >= 650:
                    matches.append((parts[0], line))
    if len(matches) == 1:
        return matches[0][0], matches[0][1]
    if not matches:
        return None, 'no unique main WeChat window with title 微信'
    return None, 'ambiguous main WeChat windows: ' + ' | '.join(line for _, line in matches)

def ocr_window_contains(win_id, expected_title):
    tmp = Path(tempfile.gettempdir()) / f'wechat_send_verify_{os.getpid()}_{int(time.time()*1000)}.png'
    try:
        r = run(['import', '-window', win_id, str(tmp)], timeout=10)
        if r.returncode != 0 or not tmp.exists():
            return False, f'import screenshot failed rc={r.returncode} stderr={r.stderr[-300:]!r}'
        import sys as _sys
        for p in (str(ROOT / 'memory' / 'L3_shared'), str(ROOT / 'memory')):
            if p not in _sys.path:
                _sys.path.insert(0, p)
        from ocr_utils import ocr_image
        res = ocr_image(str(tmp), lang='zh-Hans-CN')
        text = res.get('text') if isinstance(res, dict) else str(res)
        ok = expected_title in (text or '')
        return ok, (text or '')[:500]
    except Exception as e:
        return False, f'ocr verify exception: {e!r}'
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

def resolve_target_window(title):
    hit, line = find_window(title)
    if hit:
        return hit, line, {'matched_by': 'title'}
    title_detail = line
    hit, line = find_unique_wechat_window()
    if not hit:
        return None, f'target title not found ({title_detail}); fallback failed: {line}', None
    r = run(['wmctrl', '-ia', hit], timeout=5)
    if r.returncode != 0:
        return None, f'fallback activate failed: {r.stderr}', None
    time.sleep(.25)
    ok, detail = ocr_window_contains(hit, title)
    if not ok:
        return None, f'fallback WeChat OCR mismatch for {title!r}; ocr={detail!r}', None
    return hit, line, {'matched_by': 'wechat_main_ocr', 'ocr_preview': detail[:200]}

def parse_geometry(hit):
    geom = run(['xdotool', 'getwindowgeometry', '--shell', hit], timeout=5).stdout
    vals = {}
    for l in geom.splitlines():
        if '=' in l:
            k, v = l.split('=', 1)
            vals[k] = int(v) if v.isdigit() else v
    return vals

def safe_input_point(vals):
    return (
        int(vals.get('X', 0) + vals.get('WIDTH', 900) * 0.50),
        int(vals.get('Y', 0) + vals.get('HEIGHT', 700) * 0.93),
    )

def safe_send_button_point(vals):
    """Click the visible WeChat Send button.

    On the current Ubuntu/xwechat build Alt+S focuses/leaves the text in the
    editor instead of sending.  The button is stable at the bottom-right of the
    chat window; keep it inside the client area and use DB echo as confirmation.
    """
    return (
        int(vals.get('X', 0) + vals.get('WIDTH', 900) * 0.88),
        int(vals.get('Y', 0) + vals.get('HEIGHT', 700) * 0.955),
    )


def validate_window_geometry(vals):
    """Reject stale/off-screen geometry before any keyboard or mouse input.

    The configured group chat is intentionally a narrow, right-docked window
    (384x1053).  Width is not a safety signal here; bounds and target-window
    verification above are the safety checks.  Keep only a minimum that rules
    out a malformed geometry response.
    """
    x = int(vals.get('X', 0))
    y = int(vals.get('Y', 0))
    width = int(vals.get('WIDTH', 0))
    height = int(vals.get('HEIGHT', 0))
    if width < 280 or height < 650:
        return f'unsafe WeChat window geometry: {width}x{height}'
    screen = run(['xrandr', '--current'], timeout=5)
    match = re.search(r'current\s+(\d+)\s+x\s+(\d+)', screen.stdout or '')
    if screen.returncode != 0 or not match:
        return f'cannot verify screen geometry: rc={screen.returncode} stderr={screen.stderr[-160:]!r}'
    screen_width, screen_height = map(int, match.groups())
    if x < 0 or y < 0 or x + width > screen_width or y + height > screen_height:
        return f'WeChat window is off-screen: window={x},{y},{width}x{height} screen={screen_width}x{screen_height}'
    return None

def set_clipboard(text):
    # xclip waits for a reader when used as a clipboard owner and can keep the
    # adapter alive forever under subprocess.communicate().  Use stdin=bytes and
    # close it, then do not capture/wait on a long-lived clipboard owner.  The
    # previous owner is terminated best-effort after WeChat paste has happened.
    candidates = [
        str(PROJECT_ROOT / 'vendor' / 'xclip' / 'xclip' / 'usr' / 'bin' / 'xclip'),
        os.environ.get('XCLIP_BIN') or str(Path(__file__).resolve().parents[2] / 'tools' / 'xclip' / 'usr' / 'bin' / 'xclip'),
    ]
    xp = next((p for p in candidates if os.path.exists(p) and os.access(p, os.X_OK)), None)
    if not xp:
        raise FileNotFoundError(f'portable xclip not found; tried={candidates}')
    p = subprocess.Popen(
        [xp, '-selection', 'clipboard'],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
        env=x_env(),
    )
    assert p.stdin is not None
    p.stdin.write(text)
    p.stdin.close()
    time.sleep(0.25)
    # Verify clipboard write by reading back
    chk = run([xp, '-selection', 'clipboard', '-o'], timeout=10)
    if chk.returncode != 0 or chk.stdout != text:
        raise RuntimeError(f'clipboard verify failed: rc={chk.returncode} out={chk.stdout[:80]!r} err={chk.stderr[:120]!r}')
    return p

def release_clipboard_owner(p):
    try:
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=1)
    except Exception:
        pass

def agent_id_from_target(target):
    key = str(target.get('session_key') or '')
    if ':' in key:
        return key.split(':', 1)[0]
    return str(target.get('agent_id') or os.environ.get('WECHAT_AGENT_ID') or 'agent')

def parse_emoji_token(text):
    m = EMOJI_TOKEN_RE.match(text or '')
    if not m:
        return None
    category = (m.group(1) or '').strip()
    if not category:
        return None
    return {'category': category, 'keyword': ''}

def select_emoji_path(target, category, keyword=''):
    agent_id = agent_id_from_target(target)
    p = subprocess.run(
        ['python3', str(ADAPTER_DIR / 'emoji_library.py'), 'select', '--agent', agent_id, '--category', category, '--keyword', keyword, '--seed', f"{time.time_ns()}"],
        text=True, capture_output=True, timeout=8, cwd=str(ROOT), env=x_env()
    )
    if p.returncode != 0:
        fail(f'emoji select failed: rc={p.returncode} out={p.stdout[-500:]} err={p.stderr[-500:]}')
    data = json.loads(p.stdout or '{}')
    if not data.get('ok') or not data.get('path'):
        fail(f'emoji select failed: {data}')
    path = Path(data['path'])
    if not path.exists() or path.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
        fail(f'emoji file invalid: {path}')
    return path

def normalize_media_items(req):
    items = req.get('media') or []
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get('type') or item.get('kind') or 'image').lower()
        path_s = str(item.get('path') or item.get('file') or '').strip()
        if not path_s:
            continue
        path = Path(path_s).expanduser()
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        if kind != 'image':
            fail(f'unsupported media type for group send: {kind}')
        if not path.exists() or path.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
            fail(f'image media file invalid: {path}')
        out.append({'type': kind, 'path': path})
    return out

def set_file_clipboard(path: Path):
    # Prefer xclip file-list clipboard. WeChat/xwechat accepts Ctrl+V as image/file paste.
    candidates = [
        str(PROJECT_ROOT / 'vendor' / 'xclip' / 'xclip' / 'usr' / 'bin' / 'xclip'),
        os.environ.get('XCLIP_BIN') or str(Path(__file__).resolve().parents[2] / 'tools' / 'xclip' / 'usr' / 'bin' / 'xclip'),
    ]
    xp = next((p for p in candidates if os.path.exists(p) and os.access(p, os.X_OK)), None)
    if not xp:
        raise FileNotFoundError(f'portable xclip not found; tried={candidates}')
    uri = path.resolve().as_uri() + '\n'
    p = subprocess.Popen(
        [xp, '-selection', 'clipboard', '-t', 'text/uri-list'],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        text=True, start_new_session=True, env=x_env(),
    )
    assert p.stdin is not None
    p.stdin.write(uri)
    p.stdin.close()
    time.sleep(0.25)
    return p

def echo_check(target, text, delay_ms):
    # Poll-based echo verification: live DB can lag a few seconds behind the
    # actual send, so instead of one big sleep we split the window into short
    # polls and return as soon as the echo appears. Total window defaults to
    # max(delay_ms, 6000) so busy groups still have enough room. Each poll
    # reuses rust_db_tail.py to avoid diverging from the main read path.
    needle = (text or '').strip()
    total_ms = max(int(delay_ms or 0), 6000)
    step_ms = 500
    # small initial wait so the first tail attempt has a realistic chance to
    # see the just-sent message in live DB; use min of step and configured delay
    initial_wait = min(int(delay_ms or 0), step_ms)
    if initial_wait > 0:
        time.sleep(initial_wait / 1000.0)
    waited = initial_wait
    last_err = None
    last_count = 0
    attempts = 0
    while True:
        attempts += 1
        try:
            p = subprocess.run(
                ['python3', str(ADAPTER_DIR / 'rust_db_tail.py')],
                # Echo verification must be DB/text-only. Image enrichment can
                # spawn popup/OCR helpers and wedge the sender after physical
                # send; confirmation only needs recent rows.
                input=json.dumps({'target': target, 'cursor': '', 'limit': 200, 'allow_image_enrich': False}, ensure_ascii=False),
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8, cwd=str(ROOT),
                env=x_env()
            )
            if p.returncode != 0:
                last_err = f'db_tail rc={p.returncode} {p.stderr[-300:]}'
            else:
                data = json.loads(p.stdout or '{}')
                if data.get('adapter_error'):
                    last_err = data.get('adapter_error')
                else:
                    candidates = data.get('messages') or []
                    last_count = len(candidates)
                    for m in reversed(candidates):
                        if (m.get('text') or '').strip() == needle:
                            return {
                                'ok': True,
                                'matched_msg_id': m.get('id'),
                                'matched_ts': m.get('ts'),
                                'matched_seq': m.get('seq'),
                                'sender': m.get('sender'),
                                'is_self': m.get('is_self'),
                                'tail_limit': 200,
                                'tail_count': last_count,
                                'attempts': attempts,
                                'waited_ms': waited,
                            }
        except Exception as e:
            last_err = repr(e)
        if waited >= total_ms:
            break
        time.sleep(step_ms / 1000.0)
        waited += step_ms
    return {
        'ok': False,
        'error': last_err or 'sent text not found in recent db tail',
        'tail_limit': 200,
        'tail_count': last_count,
        'attempts': attempts,
        'waited_ms': waited,
    }

def validate_table_binding(target, title):
    live = target.get('live_db') or {}
    table = str(live.get('table') or '')
    if not table:
        return
    p = PROJECT_ROOT / 'config' / 'table_bindings.json'
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        fail(f'table binding file unreadable: {e!r}')
    bindings = data.get('bindings') or {}
    binding = bindings.get(table)
    if not binding:
        fail(f'unverified live_db.table for send: {table}')
    expected_title = str(binding.get('wechat_title') or '')
    expected_target = str(binding.get('target_id') or '')
    actual_target = str(target.get('id') or '')
    if expected_title != title:
        fail(f'table/window binding mismatch: table={table} expected_title={expected_title!r} actual_title={title!r}')
    if expected_target and actual_target and expected_target != actual_target:
        fail(f'table/target binding mismatch: table={table} expected_target={expected_target!r} actual_target={actual_target!r}')

def send_one(req, text, media_items=None, emit=True):
    target = req.get('target') or {}
    title = str(target.get('wechat_title') or '')
    delay_ms = int(req.get('echo_check_delay_ms') or target.get('echo_check_delay_ms') or 0)
    preflight_only = bool(req.get('preflight_only') or target.get('preflight_only'))
    if not title:
        return fail('empty wechat_title')
    validate_table_binding(target, title)
    hit, line, verify = resolve_target_window(title)
    if not hit:
        return fail(f'target window not found or not verified: {title}; detail={line}')
    geom_vals = parse_geometry(hit)
    geometry_error = validate_window_geometry(geom_vals)
    if geometry_error:
        return fail(geometry_error)
    x, y = safe_input_point(geom_vals)
    r = run(['wmctrl', '-ia', hit], timeout=5)
    if r.returncode != 0:
        return fail(f'wmctrl activate failed: {r.stderr}')
    time.sleep(.25)
    active = run(['xdotool', 'getactivewindow', 'getwindowname'], timeout=5).stdout.strip()
    if title not in active and not (verify and verify.get('matched_by') == 'wechat_main_ocr' and active == '微信'):
        return fail(f'active window mismatch before input: {active!r}')
    base = {
        'target_title': title,
        'window_id': hit,
        'window_line': line,
        'active_title': active,
        'window_verify': verify,
        'input_point': {'x': x, 'y': y},
        'geometry': geom_vals,
        'text_preview': text[:120],
        'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
    }
    if preflight_only:
        result = {'sent': False, 'preflight_only': True, **base}
        if emit:
            print(json.dumps(result, ensure_ascii=False))
        return result
    media_items = list(media_items or [])
    emoji = parse_emoji_token(text) if text else None
    is_emoji = bool(emoji)
    emoji_path = select_emoji_path(target, emoji['category'], emoji.get('keyword') or '') if emoji else None
    image_path = media_items[0]['path'] if media_items and not emoji_path else emoji_path
    has_image_media = bool(media_items)
    owner = set_file_clipboard(image_path) if image_path else set_clipboard(text)
    try:
        # Match the proven private-chat path: click the editor itself using a
        # window-relative point, then verify the target window again before any
        # destructive key or paste.  Activating the top-level window alone does
        # not guarantee that the WeChat editor owns keyboard focus.
        input_rel_x = min(144, max(24, int(geom_vals.get('WIDTH', 0)) - 24))
        input_rel_y = min(990, max(24, int(geom_vals.get('HEIGHT', 0)) - 24))
        click = run([
            'xdotool', 'mousemove', '--window', hit,
            str(input_rel_x), str(input_rel_y), 'click', '1'
        ], timeout=5)
        if click.returncode != 0:
            return fail(f'input click failed: {click.stderr[-200:]!r}')
        time.sleep(.3)
        active_after_click = run(['xdotool', 'getactivewindow', 'getwindowname'], timeout=5).stdout.strip()
        if title not in active_after_click and not (verify and verify.get('matched_by') == 'wechat_main_ocr' and active_after_click == '微信'):
            return fail(f'active window mismatch after input click: {active_after_click!r}')
        # A previous click on Send may have missed.  Never append a new reply to
        # stale editor content: select everything and clear before every paste.
        clear = run(['xdotool', 'key', 'ctrl+a', 'key', 'BackSpace'], timeout=5)
        if clear.returncode != 0:
            return fail(f'input clear failed: {clear.stderr[-200:]!r}')
        paste = run(['xdotool', 'key', 'ctrl+v'], timeout=5)
        if paste.returncode != 0:
            return fail(f'input paste failed: {paste.stderr[-200:]!r}')
        time.sleep(.35 if (is_emoji or has_image_media) else .2)
        active = run(['xdotool', 'getactivewindow', 'getwindowname'], timeout=5).stdout.strip()
        if title not in active and not (verify and verify.get('matched_by') == 'wechat_main_ocr' and active == '微信'):
            return fail(f'active window mismatch before send: {active!r}')
        # The narrow right-docked layout uses the same reliable path as the
        # private monitor: paste, then press Enter.  Do not click a fixed
        # button coordinate; at 384px width that coordinate is not stable.
        send_key = run(['xdotool', 'key', '--clearmodifiers', 'Return'], timeout=5)
        if send_key.returncode != 0:
            return fail(f'send enter failed: {send_key.stderr[-200:]!r}')
        time.sleep(.25)
    finally:
        release_clipboard_owner(owner)
    echo = {'ok': None, 'skipped': 'image_media_no_text_echo'} if (is_emoji or has_image_media) else (echo_check(target, text, delay_ms) if delay_ms else {'ok': None, 'skipped': True})
    if delay_ms and not (is_emoji or has_image_media) and not echo.get('ok'):
        return fail(f'post-send DB echo not confirmed: {echo!r}')
    result = {
        'sent': True,
        **base,
        'active_title_before_send': active,
        'emoji': {'used': is_emoji, 'path': str(emoji_path) if emoji_path else None, **(emoji or {})},
        'media': [{'type': m['type'], 'path': str(m['path'])} for m in media_items],
        'echo': echo,
        'echo_checked': bool(delay_ms and not (is_emoji or has_image_media)),
        'echo_confirmed': bool(echo.get('ok')),
        'echo_msg_id': echo.get('matched_msg_id'),
        'echo_source': 'live_db_tail' if delay_ms and not (is_emoji or has_image_media) else None,
    }
    if emit:
        print(json.dumps(result, ensure_ascii=False))
    return result

INTERNAL_SEND_RE = re.compile(
    r'(?:🛠️|🔧|start_long_term_update\s*\(|update_working_checkpoint\s*\(|'
    r'LLM\s+Running\s*\(Turn\s+\d+\)|^\s*Turn\s+\d+\s*\.\.\.|'
    r'Tool\s*:|args\s*:|###\s*\[WORKING MEMORY\]|<summary\b|<thinking\b|<analysis\b|'
    r'根据刚才的互动.*?核心记忆配置文件|暂未包含像.*?需要长久固化|'
    r'【FINAL_ANSWER】|\[FINAL_ANSWER\]|FINAL_ANSWER\s*:)',
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)


def _salvage_visible_tail(text: str) -> str:
    """Strip internal/pollution lines while preserving the whole visible reply.

    This function is a sender-side safety net only.  It must not decide mention
    targets and must not keep only @-prefixed lines: a single visible WeChat
    bubble may legitimately continue on following non-@ lines.
    """
    raw = (text or '').strip()
    if not raw:
        return ''
    # Remove standalone DECISION lines, without truncating the visible body after them.
    raw = re.sub(r'(?im)^\s*DECISION\s*:\s*(?:REPLY|SKIP)\b\s*$', '', raw).strip()
    kept = []
    skipping_xml_block = None
    for ln in raw.splitlines():
        s = ln.strip()
        lower = s.lower()
        if not s:
            if kept and kept[-1] != '':
                kept.append('')
            continue
        if skipping_xml_block:
            if f'</{skipping_xml_block}>' in lower:
                skipping_xml_block = None
            continue
        m = re.match(r'^<(summary|thinking|analysis)\b', lower)
        if m:
            tag = m.group(1)
            if f'</{tag}>' not in lower:
                skipping_xml_block = tag
            continue
        if re.search(r'###\s*\[WORKING MEMORY\]', s, flags=re.IGNORECASE):
            # The injected working-memory block is never user-visible content.
            # If it leaks into a candidate WeChat bubble, discard this line and
            # the following payload instead of trying to salvage fragments.
            break
        if re.search(r'(?:Tool\s*:|args\s*:|'
                     r'start_long_term_update\s*\(|update_working_checkpoint\s*\(|code_run\s*\(|file_read\s*\(|file_patch\s*\()', s,
                     flags=re.IGNORECASE):
            continue
        if re.match(r'(?i)^\s*(?:LLM\s+Running\s*\()?Turn\s+\d+', s):
            continue
        if re.match(r'(?i)^\s*DECISION\s*:\s*(?:REPLY|SKIP)\b\s*$', s):
            continue
        kept.append(ln)
    return '\n'.join(kept).strip()


def sanitize_outbound_text(text: str) -> str:
    text = strip_context_pollution((text or '').strip())
    if not text:
        return ''
    if INTERNAL_SEND_RE.search(text) or re.search(r'(?im)^\s*DECISION\s*:', text):
        text = strip_context_pollution(_salvage_visible_tail(text))
        if not text or INTERNAL_SEND_RE.search(text) or CONTEXT_POLLUTION_RE.search(text):
            return ''
    return text


def split_emoji_bubbles(text: str):
    """Split only explicit model-authored bubble boundaries and emoji tokens.

    Boundary policy: the transport layer must not decide who is being replied to
    and must not split/reshape text based on @mentions.  If multiple people need
    separate replies, the model prompt should make the model output separate
    bubbles itself (newline/Chinese semicolon), each with its own @ when needed.
    """
    out = []
    for part in re.split(r'[；\n]+', text or ''):
        part = part.strip()
        if not part:
            continue
        pos = 0
        for m in EMOJI_TOKEN_FIND_RE.finditer(part):
            before = part[pos:m.start()].strip()
            token = m.group(1).strip()
            if before:
                out.append(before)
            if token:
                out.append(token)
            pos = m.end()
        rest = part[pos:].strip()
        if rest:
            out.append(rest)
    return out


def main():
    req = json.load(sys.stdin)
    media_items = normalize_media_items(req)
    text = sanitize_outbound_text(req.get('text') or '')
    if not text and not media_items:
        return fail('empty or internal-only text blocked')
    bubbles = [sanitize_outbound_text(b.strip()) for b in split_emoji_bubbles(text) if b.strip()]
    bubbles = [b for b in bubbles if b]
    if not bubbles and not media_items:
        return fail('all bubbles blocked as internal text')
    if media_items and not bubbles:
        bubbles = ['']
    results = []
    for i, bubble in enumerate(bubbles):
        if i > 0:
            time.sleep(0.8)
        results.append(send_one(req, bubble, media_items if i == len(bubbles) - 1 else [], emit=False))
    if len(results) == 1:
        print(json.dumps(results[0], ensure_ascii=False))
    else:
        print(json.dumps({
            'sent': all(bool(r.get('sent')) for r in results),
            'bubble_count': len(results),
            'echo_confirmed': all(bool(r.get('echo_confirmed')) for r in results if r.get('echo_checked')),
            'results': results,
        }, ensure_ascii=False))

if __name__ == '__main__':
    main()
