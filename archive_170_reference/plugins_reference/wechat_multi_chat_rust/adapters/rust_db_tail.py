#!/usr/bin/env python3
"""Rust WeChat multi-chat DB tail adapter.

Contract (stdin -> stdout JSON only):
  in : {target:{id, db_identity, live_db?}, cursor, limit}
  out: {messages:[{id,seq,ts,target_id,target_identity,sender,text,is_self}]}

This adapter deliberately does not import monitor_group_v2 CONFIG, so every Rust
TargetConfig can own a different live DB/table/self_users mapping.
"""
import hashlib
import json
import os
import re
import subprocess
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from x11_display import x_env

ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = ROOT / 'plugins' / 'wechat_group_monitor'
DEFAULT_HELPER = PLUGIN_DIR / 'live_wechat_tail.py'
DEFAULT_PY = PLUGIN_DIR / '.venv_wechatdb' / 'bin' / 'python'


def decode_text(s):
    if s is None:
        return ''
    if not isinstance(s, str):
        s = str(s)
    # xwechat sometimes stores visible xml-ish text; keep conservative and let
    # upstream helper do heavy decode if it already supports it.
    return s.replace('\r\n', '\n').strip()


def pick_live_db(target):
    live = target.get('live_db') or {}
    if not isinstance(live, dict):
        live = {}
    # Backward-compatible top-level aliases for easier config authoring.
    for k in ('path', 'table', 'db_key', 'helper_python', 'helper_script'):
        if not live.get(k) and target.get(k):
            live[k] = target.get(k)
    if not live.get('helper_python'):
        live['helper_python'] = str(DEFAULT_PY)
    if not live.get('helper_script'):
        live['helper_script'] = str(DEFAULT_HELPER)
    if not live.get('table'):
        live['table'] = target.get('db_table') or 'MSG'
    return live


def run_tail(live, after, limit):
    db = Path(str(live.get('path') or live.get('db_path') or ''))
    helper_py = Path(str(live.get('helper_python') or DEFAULT_PY))
    helper_script = Path(str(live.get('helper_script') or DEFAULT_HELPER))
    table = str(live.get('table') or 'MSG')
    if not db.exists():
        raise FileNotFoundError(f'live_db.path not found: {db}')
    if not helper_py.exists():
        raise FileNotFoundError(f'helper_python not found: {helper_py}')
    if not helper_script.exists():
        raise FileNotFoundError(f'helper_script not found: {helper_script}')
    cmd = [str(helper_py), str(helper_script), '--db', str(db), '--table', table, '--after-seq', str(int(after or 0)), '--limit', str(int(limit or 0))]
    if live.get('db_key'):
        cmd.extend(['--db-key', str(live.get('db_key'))])
    if live.get('self_users'):
        cmd.extend(['--self-users', ','.join(str(x) for x in (live.get('self_users') or []))])
    if live.get('peer_users'):
        cmd.extend(['--peer-users', ','.join(str(x) for x in (live.get('peer_users') or []))])
    if live.get('self_label'):
        cmd.extend(['--self-label', str(live.get('self_label'))])
    if live.get('peer_label'):
        cmd.extend(['--peer-label', str(live.get('peer_label'))])
    if live.get('target_chatroom') or live.get('chatroom_id'):
        cmd.extend(['--target-chatroom', str(live.get('target_chatroom') or live.get('chatroom_id'))])
    p = subprocess.run(cmd, cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=int(live.get('timeout_s') or 30))
    if p.returncode != 0:
        raise RuntimeError(f'live tail rc={p.returncode} stderr={p.stderr[-1200:]}')
    data = json.loads(p.stdout or '{}')
    if not data.get('ok'):
        raise RuntimeError(f'live tail failed: {data.get("error") or data}')
    return data.get('rows') or []


def row_matches(row, ident):
    if not ident:
        return True
    # live_wechat_tail.py is already scoped to one message table. Some xwechat
    # exports do not include room/talker/title fields in returned rows; in that
    # case filtering against sender names would incorrectly drop every message.
    conv_fields = ('room', 'talker', 'session', 'strTalker', 'receiver', 'chatroom', 'group_name', 'wechat_title')
    hay = ' '.join(str(row.get(k) or '') for k in conv_fields).strip()
    if not hay:
        return True
    return ident in hay or hay in ident


def _stable_self_display(wechat_display, self_label=None):
    """Prefer configured agent display_name for self bubbles.

    Shared WeChat accounts change nicknames (AGENT_DISPLAY_NAME/AGENT_DISPLAY_NAME/OTHER_AGENT_DISPLAY_NAME/OTHER_AGENT_DISPLAY_NAME...). The model should
    see a stable role label, not the ephemeral WeChat nick. Internal agent ids
    like agent_alpha/agent_beta must never become the visible self display.
    """
    label = str(self_label or '').strip()
    raw = str(wechat_display or '').strip()
    # Only block internal ids on the *configured* label. WeChat nick "OTHER_AGENT_DISPLAY_NAME" is real.
    blocked_labels = {'self', 'agent_alpha', 'agent_beta', 'agent_gamma', 'main', 'agent', ''}
    if label and label.lower() not in blocked_labels:
        return label
    if raw and raw not in {'我', '本账号', '本账号/我', 'self', 'unknown'}:
        return raw
    return ''


def _format_reply_context(ctx, self_users=None, self_label=None):
    """Render helper-provided WeChat quote metadata into the message text.

    Critical: quote author must be labeled by account (quoted_user ∈ self_users),
    not bare display name. Otherwise quotes of self bubbles (nick=AGENT_DISPLAY_NAME/OTHER_AGENT_DISPLAY_NAME)
    look like "someone else" and the model treats them as 对方.
    """
    if not isinstance(ctx, dict) or not ctx:
        return ''
    quoted_user = str(ctx.get('quoted_user') or '').strip()
    quoted_display = str(ctx.get('quoted_display') or quoted_user or '原消息').strip()
    quoted_content = str(ctx.get('quoted_content') or '').strip()
    reply_title = str(ctx.get('reply_title') or '').strip()
    self_set = {str(x).strip() for x in (self_users or []) if str(x).strip()}
    is_quoted_self = bool(quoted_user and quoted_user in self_set)
    if is_quoted_self:
        # Stable first-person quote label; prefer configured role name over nick.
        stable = _stable_self_display(quoted_display, self_label)
        if stable and stable not in {'我', '本账号', '本账号/我', '原消息'}:
            quoted_label = f"【本账号/我|原显示名={stable}】"
        else:
            quoted_label = "【本账号/我】"
    else:
        quoted_label = quoted_display or '原消息'
    parts = []
    if quoted_content:
        parts.append(f"引用 {quoted_label}: {quoted_content}")
    elif reply_title:
        parts.append(f"引用 {quoted_label}: {reply_title}")
    media = ctx.get('quoted_media')
    if isinstance(media, dict) and media:
        kind = str(media.get('kind') or 'media')
        hints = []
        for k in ('md5', 'cdnthumburl', 'length', 'cdnthumblength'):
            v = str(media.get(k) or '').strip()
            if v:
                hints.append(f"{k}={v[:80]}")
        parts.append(f"引用媒体({kind}{'; ' + '; '.join(hints) if hints else ''})")
    return '；'.join(parts)


def _format_at_users(at_users, self_users=None):
    """Embed official @ targets into message text for main model.

    Uses account id (wxid/username) vs self_users — never display nick alone —
    so high-freq path can tell 「@AGENT_DISPLAY_NAME」is this account or another same-name peer.
    Empty / missing at_users → no annotation (plain text @ stays as-is).
    """
    if not at_users:
        return ''
    if isinstance(at_users, str):
        raw_list = [x.strip() for x in re.split(r'[,;，\s]+', at_users) if x.strip()]
    elif isinstance(at_users, (list, tuple, set)):
        raw_list = [str(x).strip() for x in at_users if str(x).strip()]
    else:
        return ''
    if not raw_list:
        return ''
    self_set = {str(x).strip() for x in (self_users or []) if str(x).strip()}
    labels = []
    seen = set()
    for u in raw_list:
        if u in seen:
            continue
        seen.add(u)
        if u in self_set:
            labels.append(f'@本账号({u})')
        else:
            labels.append(f'@非本账号({u})')
    if not labels:
        return ''
    return '、'.join(labels)


def row_to_msg(row, target, ident, self_users, self_label=None):
    seq = int(row.get('local_id') or row.get('seq') or row.get('id') or 0)
    text = decode_text(
        row.get('content')
        or row.get('message_content')
        or row.get('text')
        or row.get('msg')
        or ''
    )
    reply_text = _format_reply_context(
        row.get('reply_context'), self_users=self_users, self_label=self_label
    )
    if reply_text:
        text = f"{text}\n[微信引用] {reply_text}" if text else f"[微信引用] {reply_text}"
    at_text = _format_at_users(row.get('at_users'), self_users=self_users)
    if at_text:
        text = f"{text}\n[微信@] {at_text}" if text else f"[微信@] {at_text}"
    sender_user = str(
        row.get('sender_user')
        or row.get('sender')
        or row.get('from_user')
        or row.get('real_sender_id')
        or ''
    )
    sender = str(row.get('sender_display') or row.get('nickname') or sender_user or 'unknown')
    time_s = str(row.get('time') or row.get('create_time') or '')
    if time_s.isdigit() and len(time_s) in (10, 13):
        try:
            ts = int(time_s)
            if len(time_s) == 13:
                ts = ts // 1000
            time_s = time.strftime('%F %T', time.localtime(ts))
        except Exception:
            pass
    if not time_s:
        time_s = time.strftime('%F %T')
    is_self = sender_user in self_users or sender in self_users or bool(row.get('is_self'))
    if is_self:
        # Keep structured sender as stable role label for inject/main formatting.
        stable = _stable_self_display(sender, self_label)
        if stable:
            sender = stable
    mid = hashlib.sha256(f"{target.get('id')}|{ident}|{seq}|{time_s}|{sender}|{text}".encode('utf-8')).hexdigest()
    return {'id': mid, 'seq': seq, 'ts': time_s, 'target_id': target.get('id'), 'target_identity': ident, 'sender': sender, 'sender_user': sender_user, 'text': text, 'is_self': is_self}


_IMG_CACHE_DIR = '/tmp/rust_img_enrich'
_ADAPTER_DIR = os.path.dirname(os.path.abspath(__file__))
_IMG_ADAPTER = os.path.join(_ADAPTER_DIR, 'rust_image_click_popup.py')


def _has_image_marker(text):
    if not text:
        return False
    # xwechat helper may expose image messages as one of:
    #   [图片: /path/to/file]   (attach resolved)
    #   [图片 ...]              (legacy marker with hints)
    #   [图片]                  (private chat image before attach path resolves)
    #   引用媒体(image...)      (quoted/replied image)
    # The exact '[图片]' form must still trigger window-crop OCR+vision.
    return (
        ('[图片:' in text)
        or ('[图片 ' in text)
        or (text.strip() == '[图片]')
        or (text.strip() == '[表情]')
        or ('引用媒体(image' in text)
    )


def _resolve_window_geom(title):
    """用 wmctrl 按窗口标题精确匹配,返回 (wid, [x,y,w,h]) 或 None."""
    if not title:
        return None
    try:
        out = subprocess.run(
            ['wmctrl', '-lG'], capture_output=True, text=True, timeout=5, env=x_env()
        ).stdout
        for line in out.splitlines():
            parts = line.split(None, 7)
            if len(parts) < 8:
                continue
            wid, _desk, x, y, w, h, _host, wtitle = parts
            if wtitle.strip() == title.strip():
                return wid, [int(x), int(y), int(w), int(h)]
    except Exception:
        return None
    return None


def _load_cache(target_id):
    os.makedirs(_IMG_CACHE_DIR, exist_ok=True)
    p = os.path.join(_IMG_CACHE_DIR, f'{target_id}.json')
    if os.path.isfile(p):
        try:
            return json.load(open(p, 'r', encoding='utf-8'))
        except Exception:
            return {}
    return {}


def _save_cache(target_id, cache):
    p = os.path.join(_IMG_CACHE_DIR, f'{target_id}.json')
    try:
        # 只保留最近 200 条, 防止无限增长
        keys = list(cache.keys())
        if len(keys) > 200:
            for k in keys[:-200]:
                cache.pop(k, None)
        json.dump(cache, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    except Exception:
        pass


def _run_image_adapter(wid, win_geom, storage_dir, timeout=90):
    cfg = {
        'wid': wid,
        'win_geom': win_geom,
        # rust_image_click_popup.py consumes screenshots_dir; keep storage_dir
        # only for backward readability.
        'screenshots_dir': storage_dir,
        'storage_dir': storage_dir,
        # Non-clawbot image flow must produce detailed per-image descriptions,
        # not OCR-only. The helper captures the target WeChat window, uses vision
        # to locate visible image blocks, then crops those blocks from that same
        # window screenshot and runs OCR + vision. It must not click images or
        # capture the root desktop.
        'do_vision': True,
        'do_summary': True,
        'vision_model': 'gemini-3-flash-preview',
        'vision_prompt': (
            '请直接根据图片本身进行自然分析和描述。按图片类型选择合适的重点：照片就描述画面，'
            '海报/截图/聊天/文档就保留标题、文字、排版、视觉重点和关键信息，表情包/梗图就说明可见内容和情绪。'
            '不要套固定格式，不要为了补全细节而臆测；看不清或不确定的地方直接说明。'
        ),
    }
    try:
        r = subprocess.run(
            [sys.executable, _IMG_ADAPTER],
            input=json.dumps(cfg), text=True,
            capture_output=True, timeout=timeout,
        )
        return json.loads(r.stdout or '{}')
    except Exception as e:
        return {'ok': False, 'error': repr(e)}


def _format_click_description(c):
    """Build the exact text injected into ChatMsg for one cropped image.

    Historical field names are kept for adapter compatibility: c['opened'] means
    crop succeeded in the current no-click flow.
    """
    if not c or not c.get('opened'):
        return ''
    tag = '引用图' if c.get('type') == 'quote' else '图片'
    idx = int(c.get('idx') or 0) + 1
    parts = [f'{tag}{idx}']
    note = str(c.get('note') or '').strip()
    if note:
        parts.append(f'定位: {note}')
    ocr = str(c.get('ocr_text') or '').strip()
    if ocr:
        if len(ocr) > 800:
            ocr = ocr[:800] + '...'
        parts.append('文字识别: ' + ocr)
    vis = str(c.get('vision_text') or '').strip()
    if vis:
        parts.append('画面观察: ' + vis)
    return '\n'.join(parts) if len(parts) > 1 else ''


def _dedupe_clicks(clicks):
    """Drop duplicate crops from one window screenshot.

    The locator model may return several nearby centers for the same visible
    WeChat image/sticker.  Without this guard every duplicate candidate runs OCR
    + vision and the same picture is injected multiple times.
    """
    out = []
    seen_pos = set()
    seen_text = set()
    for c in clicks or []:
        if not c or not c.get('opened'):
            continue
        try:
            # crop_window_block uses a 300x300 normal box / 290x140 quote box;
            # bucket nearby centers so duplicate locator points collapse.
            pos_key = (str(c.get('type') or 'normal'), round(int(c.get('x_screen') or 0) / 80), round(int(c.get('y_screen') or 0) / 80))
        except Exception:
            pos_key = (str(c.get('type') or 'normal'), str(c.get('x_screen') or ''), str(c.get('y_screen') or ''))
        text_key = hashlib.sha1(((c.get('ocr_text') or '') + '\n' + (c.get('vision_text') or '')).strip().encode('utf-8')).hexdigest()
        if pos_key in seen_pos or (text_key in seen_text and ((c.get('ocr_text') or c.get('vision_text')))):
            continue
        seen_pos.add(pos_key)
        if c.get('ocr_text') or c.get('vision_text'):
            seen_text.add(text_key)
        out.append(c)
    return out


def enrich_images(target, msgs):
    """Enrich non-clawbot WeChat image messages.

    Required flow, kept deliberately in this one chain:
    DB sees image marker -> screenshot target WeChat window -> vision locates all
    visible image/quoted-image blocks -> crop each visible block in order from
    that same window screenshot -> OCR + vision
    detailed description -> inject per-image text back into the corresponding
    ChatMsg before Rust model routing.
    """
    img_msgs = [m for m in msgs if _has_image_marker(m.get('text'))]
    if not img_msgs:
        return

    target_id = str(target.get('id') or 'unknown')
    cache = _load_cache(target_id)

    # Re-attach successful cached descriptions whenever an image marker is
    # present in the current prompt window.  The model only sees the current
    # ChatMsg text; if we drop cached descriptions after the first scan, later
    # replies become intermittent: the same [图片] is visible in DB context but
    # no longer carries [识图].  Empty cache entries are treated as retryable
    # failures, not permanent "seen" markers.
    for m in img_msgs:
        seq = str(m.get('seq'))
        cached_desc = str(cache.get(seq) or '').strip()
        if cached_desc and '[识图]' not in str(m.get('text') or ''):
            m['text'] = m['text'] + '\n[识图]\n' + cached_desc

    pending = [m for m in img_msgs if not str(cache.get(str(m.get('seq'))) or '').strip()]
    if not pending:
        return

    title = str(target.get('wechat_title') or '')
    wg = _resolve_window_geom(title)
    if not wg:
        return  # 窗口找不到,静默跳过，避免阻塞文字链路
    wid, win_geom = wg

    storage = os.path.join(_IMG_CACHE_DIR, f'shots_{target_id}_{int(time.time())}')
    try:
        result = _run_image_adapter(wid, win_geom, storage)
        clicks = _dedupe_clicks(result.get('clicks') or [])
        descs = [_format_click_description(c) for c in clicks]
        descs = [d for d in descs if d]

        if not descs:
            # 没定位/没裁出有效内容：不写永久空缓存，允许后续窗口/点击状态恢复后重试。
            return

        # 对齐策略：adapter 返回窗口内可见图片按从上到下/引用优先的顺序；
        # DB pending 也是按 seq 从旧到新。若可见图多于本轮 pending，取最后 N 张对应本轮新增图片。
        use_descs = descs[-len(pending):] if len(descs) >= len(pending) else descs
        if len(use_descs) == len(pending):
            for m, d in zip(pending, use_descs):
                m['text'] = m['text'] + '\n[识图]\n' + d
                cache[str(m.get('seq'))] = d
        else:
            # 数量不一致时不能乱配多条消息：把聚合结果挂到最新图片，其他仅标记处理。
            aggregated = '\n\n'.join(use_descs)
            latest = pending[-1]
            latest['text'] = latest['text'] + '\n[识图]\n' + aggregated
            cache[str(latest.get('seq'))] = aggregated
            for m in pending[:-1]:
                cache[str(m.get('seq'))] = ''

        # Cached old image messages are intentionally not appended again. New images
        # get one enrichment at the moment they first appear; later polling windows
        # keep only the original [图片] marker to avoid repeated reads.
        _save_cache(target_id, cache)
    finally:
        # OCR/Vision text is kept in JSON cache; raw window/crop PNGs are transient.
        shutil.rmtree(storage, ignore_errors=True)

def main():
    req = json.load(sys.stdin)
    target = req.get('target') or {}
    cursor = str(req.get('cursor') or '')
    after = int(cursor) if cursor.isdigit() else 0
    limit = int(req.get('limit') or 80)
    allow_image_enrich = bool(req.get('allow_image_enrich', True))
    ident = str(target.get('db_identity') or target.get('wechat_title') or '')
    try:
        live = pick_live_db(target)
        live.setdefault('self_users', target.get('self_users') or [])
        live.setdefault('peer_users', target.get('peer_users') or [])
        # Prefer agent.display_name (AGENT_DISPLAY_NAME). Never default to internal id "xinghui"/literal "self".
        # Fallback: old binaries may not pass agent; read local config.json.
        agent_display = str(((req.get('agent') or {}) if isinstance(req.get('agent'), dict) else {}).get('display_name') or '').strip()
        if not agent_display or agent_display.lower() in {'self', 'agent_alpha', 'agent_beta', 'agent_gamma', 'main', 'agent'}:
            try:
                cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'config.json')
                with open(cfg_path, 'r', encoding='utf-8') as cf:
                    cfg_agent = (json.load(cf).get('agent') or {})
                agent_display = str(cfg_agent.get('display_name') or '').strip()
            except Exception:
                pass
        # Force stable role label over live_db/wechat nick (AGENT_DISPLAY_NAME/OTHER_AGENT_DISPLAY_NAME/... may rotate).
        if agent_display and agent_display.lower() not in {'self', 'agent_alpha', 'agent_beta', 'agent_gamma', 'main', 'agent'}:
            live['self_label'] = agent_display
        else:
            live.setdefault('self_label', agent_display)
        aliases = (target.get('rules') or {}).get('user_aliases') or []
        if aliases:
            live.setdefault('peer_label', aliases[0])
        self_users = set(live.get('self_users') or target.get('self_users') or [])
        self_label = str(live.get('self_label') or agent_display or '').strip()
        if self_label.lower() in {'self', 'agent_alpha', 'agent_beta', 'agent_gamma', 'main', 'agent'}:
            self_label = agent_display if agent_display.lower() not in {'self', 'agent_alpha', 'agent_beta', 'agent_gamma', 'main', 'agent'} else ''
        rows = run_tail(live, after, limit)
        msgs = []
        for row in rows:
            if ident and not row_matches(row, ident):
                continue
            msg = row_to_msg(row, target, ident, self_users, self_label=self_label)
            if msg['text']:
                msgs.append(msg)
        msgs.sort(key=lambda m: int(m.get('seq') or 0))
        try:
            if allow_image_enrich:
                enrich_images(target, msgs)
        except Exception as ie:
            # 识图链路失败不能阻塞主流程
            msgs_meta = {'enrich_images_error': repr(ie)}
        else:
            msgs_meta = {}
        out = {'messages': msgs}
        if msgs_meta:
            out.update(msgs_meta)
        print(json.dumps(out, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({'messages': [], 'adapter_error': repr(e), 'target': target.get('id')}, ensure_ascii=False))

if __name__ == '__main__':
    main()
