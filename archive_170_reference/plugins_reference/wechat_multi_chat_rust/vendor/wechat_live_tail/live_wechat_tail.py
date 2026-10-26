#!/usr/bin/env python3
import argparse, pathlib, tempfile, shutil, os, json, datetime, binascii, re, html
import sqlcipher3, zstandard as zstd

DBKEYS = {}
# Optional private overrides for local deployments. Keep this file untracked:
#   vendor/wechat_live_tail/dbkeys.local.json
# Format: {"/absolute/path/message_0.db": "hex_sqlcipher_key"}
try:
    _local_dbkeys_path = pathlib.Path(__file__).with_name('dbkeys.local.json')
    if _local_dbkeys_path.exists():
        _local_dbkeys = json.loads(_local_dbkeys_path.read_text(encoding='utf-8'))
        if isinstance(_local_dbkeys, dict):
            DBKEYS.update({str(k): str(v) for k, v in _local_dbkeys.items() if v})
except Exception:
    pass
CONTACTS_RAW_JSON = pathlib.Path(os.environ.get('WECHAT_CONTACTS_RAW_JSON', pathlib.Path(__file__).resolve().parents[4] / 'temp' / 'wechat_readable_export' / 'contacts_raw.json'))
CONTACTS_RAW_JSON_CANDIDATES = [
    CONTACTS_RAW_JSON,
    pathlib.Path(__file__).resolve().parents[3] / 'temp' / 'wechat_readable_export' / 'contacts_raw.json',
]
TARGET_CHATROOM = os.environ.get('WECHAT_TAIL_TARGET_CHATROOM', '')

# Optional identity label overrides. Keep defaults generic so the plugin can be redistributed.
# Runtime callers should pass --self-users/--peer-users and --self-label/--peer-label from config.
SELF_USERS = set(filter(None, os.environ.get('WECHAT_SELF_USERS', '').split(',')))
PEER_USERS = set(filter(None, os.environ.get('WECHAT_PEER_USERS', '').split(',')))
DEFAULT_SELF_LABEL = os.environ.get('WECHAT_SELF_LABEL', 'self')
DEFAULT_PEER_LABEL = os.environ.get('WECHAT_PEER_LABEL', 'peer')

def media_label(local_type):
    try:
        t = int(local_type or 0)
    except Exception:
        t = 0
    # WeChat common message types observed in liveDB.
    if t == 1:
        return ''
    if t == 3:
        return '[图片]'
    if t == 43:
        return '[视频]'
    if t == 47:
        return '[表情]'
    if t in (49, 244813135921):
        return ''  # appmsg/card; clean_message extracts title/description when possible.
    return f'[非文本消息 type={t}]' if t else '[非文本消息]'

def _xml_get_text(s, tag):
    m = re.search(r'<%s>\s*(.*?)\s*</%s>' % (re.escape(tag), re.escape(tag)), s or '', re.S|re.I)
    if not m:
        return ''
    return html.unescape(m.group(1).strip())

def _compact_text(s, limit=260):
    s = html.unescape(s or '')
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:limit]

def extract_at_users(source_raw):
    """Parse official @ targets from msgsource.atuserlist.

    Observed forms:
      <atuserlist>wxid_xxx</atuserlist>
      <atuserlist><![CDATA[wxid_a,wxid_b]]></atuserlist>
      <atuserlist><![CDATA[]]></atuserlist>  (empty)
    Returns a de-duplicated list of usernames/wxids (order preserved).
    """
    src = source_raw or ''
    if not src or 'atuserlist' not in src.lower():
        return []
    m = re.search(r'<atuserlist[^>]*>(.*?)</atuserlist>', src, re.S | re.I)
    if not m:
        return []
    body = m.group(1) or ''
    body = re.sub(r'<!\[CDATA\[|\]\]>', '', body)
    body = html.unescape(body).strip()
    if not body:
        return []
    out = []
    seen = set()
    for part in re.split(r'[,;，\s]+', body):
        u = (part or '').strip()
        if not u or u in seen:
            continue
        # skip pure markup leftovers
        if u.startswith('<') or u.startswith('!'):
            continue
        seen.add(u)
        out.append(u)
    return out


def extract_reply_context(raw, base_dir=None, chat_id=None):
    """Return quote metadata for WeChat quoted reply appmsg(type=57)."""
    raw = raw or ''
    if '<refermsg' not in raw and '<appmsg' not in raw:
        return None
    title = _compact_text(_xml_get_text(raw, 'title'), 220)
    ref = re.search(r'<refermsg>\s*(.*?)\s*</refermsg>', raw, re.S|re.I)
    if not ref:
        return {'reply_title': title} if title else None
    block = ref.group(1)
    display = _compact_text(_xml_get_text(block, 'displayname'), 80)
    chatusr = _compact_text(_xml_get_text(block, 'chatusr'), 80)
    content = html.unescape(_xml_get_text(block, 'content'))
    rtype = _xml_get_text(block, 'type').strip()
    quoted_media = None
    if rtype == '3' or '<img ' in content or '<img>' in content:
        content = '[图片]'
        # 尝试用本地缓存匹配物理路径
        ct_str = _xml_get_text(block, 'createtime').strip()
        if base_dir and chat_id and ct_str.isdigit():
            guessed = guess_local_image_path(base_dir, chat_id, int(ct_str), 3)
            if guessed:
                content = f'[图片: {guessed}]'
        img_m = re.search(r'<img\b([^>]*)>', content if '<img' in content else html.unescape(_xml_get_text(block, 'content')), re.S|re.I)
        if img_m:
            attrs = {}
            for am in re.finditer(r'([a-zA-Z0-9_:-]+)\s*=\s*[\"\'](.*?)[\"\']', img_m.group(1), re.S):
                k = am.group(1).lower()
                v = html.unescape(am.group(2) or '').strip()
                if k in ('md5','cdnthumburl','cdnthumbaeskey','aeskey','length','cdnthumblength') and v:
                    attrs[k] = v[:260]
            if attrs:
                quoted_media = {'kind': 'image', **attrs}
    elif rtype == '47':
        content = '[表情]'
    else:
        content = _compact_text(content, 300)
    ctx = {
        'reply_title': title,
        'quoted_display': display or chatusr,
        'quoted_user': chatusr,
        'quoted_content': content,
    }
    for tag in ('createtime','svrid'):
        v = _xml_get_text(block, tag).strip()
        if v:
            ctx['quoted_' + tag] = v[:80]
    if quoted_media:
        ctx['quoted_media'] = quoted_media
    return ctx

import time
import os
from pathlib import Path

def guess_local_image_path(base_dir, chat_id, ts, local_type):
    if local_type not in (3, 47): # 只匹配图片和表情
        return None
    try:
        t = time.localtime(ts)
        month_str = time.strftime('%Y-%m', t)
        img_dir = Path(base_dir) / 'msg/attach' / chat_id / month_str / ('Img' if local_type == 3 else 'CustomEmoticon')
        if not img_dir.exists():
            return None
        
        # 寻找最近的且不是 _t.dat 结尾的大图
        best_diff = 60
        best_file = None
        for p in img_dir.glob('*.dat'):
            if '_t.' in p.name or '_b.' in p.name:
                continue
            diff = abs(p.stat().st_mtime - ts)
            if diff < best_diff:
                best_diff = diff
                best_file = str(p)
        return best_file
    except Exception as e:
        return None

def clean_message(s, local_type=None):
    s = (s or '').replace('\n', '\n').strip()
    label = media_label(local_type)
    m = re.match(r'^[^:\n：]{1,100}[:：]\s*\n(.*)$', s, re.S)
    if m:
        s = m.group(1).strip()
    if '<msg' in s or '<appmsg' in s:
        title = re.search(r'<title>\s*(.*?)\s*</title>', s, re.S|re.I)
        des = re.search(r'<des>\s*(.*?)\s*</des>', s, re.S|re.I)
        parts = [x.group(1).strip() for x in (title, des) if x and x.group(1).strip()]
        if parts:
            return ('[卡片/引用] ' + ' / '.join(parts))[:500]
        return label or '[非文本消息]'
    s = re.sub(r'<[^>]+>', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    if label and (not s or s.startswith('HEX:')):
        return label
    if label and s:
        return (label + ' ' + s)[:500]
    return s[:500]

def dec(v):
    if v is None: return ''
    if isinstance(v, str): return v
    b = bytes(v)
    if b.startswith(b'\x28\xb5\x2f\xfd'):
        try: b = zstd.ZstdDecompressor().decompress(b)
        except Exception as e: return '[ZSTD_FAIL] ' + repr(e)
    for enc in ('utf-8','utf-16le','gb18030'):
        try:
            s = b.decode(enc)
            if sum(ch.isprintable() or ch in '\r\n\t' for ch in s)/max(1,len(s)) > 0.7:
                return s.replace('\x00','')
        except Exception:
            pass
    return 'HEX:' + binascii.hexlify(b[:80]).decode()

def _read_varint(data, i):
    res = 0
    shift = 0
    while i < len(data):
        c = data[i]
        i += 1
        res |= (c & 0x7f) << shift
        if c < 128:
            return res, i
        shift += 7
    raise ValueError('varint eof')

def _parse_proto_fields(data):
    i = 0
    fields = []
    while i < len(data):
        try:
            key, i = _read_varint(data, i)
        except Exception:
            break
        fn = key >> 3
        wt = key & 7
        if wt == 0:
            try:
                val, i = _read_varint(data, i)
            except Exception:
                break
            fields.append((fn, wt, val))
        elif wt == 2:
            try:
                ln, i = _read_varint(data, i)
            except Exception:
                break
            val = data[i:i+ln]
            i += ln
            fields.append((fn, wt, val))
        elif wt == 1:
            fields.append((fn, wt, data[i:i+8]))
            i += 8
        elif wt == 5:
            fields.append((fn, wt, data[i:i+4]))
            i += 4
        else:
            break
    return fields

def parse_chatroom_members_from_ext(ext_buffer):
    """Parse WeChat chat_room.ext_buffer exported as a mojibake str.
    Observed schema: top-level repeated length-delimited member message;
    member field 1=user(wxid/alias), field 2=room display card, field 4=inviter.
    """
    if not ext_buffer:
        return {}
    if isinstance(ext_buffer, str):
        data = ext_buffer.encode('utf-16le', 'surrogatepass')
    elif isinstance(ext_buffer, bytes):
        data = ext_buffer
    else:
        return {}
    out = {}
    for fn, wt, val in _parse_proto_fields(data):
        if wt != 2 or not isinstance(val, (bytes, bytearray)):
            continue
        member = {}
        for sfn, swt, sval in _parse_proto_fields(bytes(val)):
            if swt == 2:
                try:
                    ss = bytes(sval).decode('utf-8').strip()
                except Exception:
                    continue
                if sfn == 1:
                    member['user'] = ss
                elif sfn == 2:
                    member['display'] = ss
                elif sfn == 4:
                    member['inviter'] = ss
            elif swt == 0:
                member[f'v{sfn}'] = sval
        user = member.get('user')
        if user:
            out[user] = member
    return out

def resolve_contacts_raw_json(path=None):
    """Return the first existing contacts_raw.json candidate.
    Env WECHAT_CONTACTS_RAW_JSON still has highest priority; fallback keeps old exported contact/card maps usable.
    """
    candidates = []
    if path:
        candidates.append(pathlib.Path(path))
    candidates.extend(CONTACTS_RAW_JSON_CANDIDATES)
    seen = set()
    for p in candidates:
        p = pathlib.Path(p)
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.exists():
            return p
    return pathlib.Path(path) if path else CONTACTS_RAW_JSON

def load_contact_display_map(path=None, self_label=DEFAULT_SELF_LABEL, peer_label=DEFAULT_PEER_LABEL, target_chatroom=None):
    """Build username/wxid -> display name from contact export.
    Static export is used as name/card map only; live messages still come from DB.
    In chatroom context, prefer that chatroom's member card over global contact nickname.
    """
    path = resolve_contacts_raw_json(path)
    mp = {}
    rows = []
    try:
        rows = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        rows = []
    if isinstance(rows, list):
        for r in rows:
            if not isinstance(r, dict):
                continue
            uname = r.get('username') or r.get('user_name') or r.get('wxid') or r.get('userName')
            if not uname:
                continue
            remark = (r.get('remark') or r.get('con_remark') or '').strip()
            nick = (r.get('nick_name') or r.get('nickname') or '').strip()
            alias = (r.get('alias') or '').strip()
            disp = remark or nick or alias
            if disp:
                mp[str(uname)] = disp
            elif str(uname) not in mp:
                mp[str(uname)] = str(uname)

        # Group display cards are more reliable than global contact nicknames for chatroom context.
        for r in rows:
            if not isinstance(r, dict):
                continue
            if r.get('table') == 'chat_room' and (not target_chatroom or r.get('username') == target_chatroom):
                for user, member in parse_chatroom_members_from_ext(r.get('ext_buffer')).items():
                    card = (member.get('display') or '').strip()
                    if card:
                        mp[user] = card
                    elif user not in mp:
                        mp[user] = user

    for u in PEER_USERS:
        old = mp.get(u, u)
        mp[u] = peer_label if old in (u, peer_label) or str(old).startswith(peer_label + '/') else f'{peer_label}/{old}'
    for u in SELF_USERS:
        old = mp.get(u, u)
        mp[u] = self_label if old in (u, self_label) or str(old).startswith(self_label + '/') else f'{self_label}/{old}'
    return mp

def display_for(sender, cmap):
    if not sender:
        return '未知发送者'
    if sender in cmap:
        disp = str(cmap[sender] or '').strip()
        if disp and disp != sender:
            return disp
    if sender.startswith('wxid_'):
        return f'群友({sender[-8:]})'
    return sender

def tail(db, table, limit, after_seq=0, self_users=None, peer_users=None, self_label=DEFAULT_SELF_LABEL, peer_label=DEFAULT_PEER_LABEL, db_key=None, target_chatroom=None):
    dbp = pathlib.Path(db)
    key = db_key or os.environ.get('WECHAT_TAIL_DB_KEY') or DBKEYS.get(str(dbp))
    if not key:
        raise RuntimeError('missing SQLCipher db key: pass --db-key or set WECHAT_TAIL_DB_KEY')
    contact_map = load_contact_display_map(None, self_label, peer_label, target_chatroom)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db'); tmp.close()
    try:
        shutil.copy2(dbp, tmp.name)
        con = sqlcipher3.connect(tmp.name); con.row_factory = sqlcipher3.Row
        con.execute(f"PRAGMA key = \"x'{key}'\";")
        con.execute('select count(*) from sqlite_master').fetchone()
        name2id = {}
        try:
            name2id = {int(r['rowid']): r['user_name'] for r in con.execute('select rowid,* from Name2Id')}
            for wxid in name2id.values():
                if wxid and wxid not in contact_map:
                    contact_map[wxid] = wxid[-8:] if len(wxid) > 8 else wxid
        except Exception:
            pass
        rows = []
        after_seq = int(after_seq or 0)
        limit = int(limit or 0)
        # include source for official @ target list (atuserlist)
        _cols = 'local_id, create_time, real_sender_id, message_content, local_type, source'
        if after_seq > 0:
            if limit > 0:
                q = f'select {_cols} from "{table}" where local_id > ? order by local_id asc limit ?'
                cur = con.execute(q, (after_seq, limit))
            else:
                q = f'select {_cols} from "{table}" where local_id > ? order by local_id asc'
                cur = con.execute(q, (after_seq,))
        else:
            q = f'select {_cols} from "{table}" order by create_time desc, local_id desc limit ?'
            cur = con.execute(q, (limit if limit > 0 else 32,))
        for r in cur:
            sender = name2id.get(int(r['real_sender_id']), str(r['real_sender_id'])) if r['real_sender_id'] is not None else ''
            raw_content = dec(r['message_content'])
            
            content = clean_message(raw_content, r['local_type'])
            # db_path example: /path/to/xwechat_files/wxid_example/db_storage/message/message_0.db
            # base_dir example: /path/to/xwechat_files/wxid_example
            base_dir = str(Path(dbp).parent.parent.parent)
            # table 类似 Msg_94041ecc6b1beb6c841a1cc0dcc5bc91
            chat_id = table[4:] if table.startswith('Msg_') else table

            if r['local_type'] in (3, 47):
                guessed_path = guess_local_image_path(base_dir, chat_id, r['create_time'], r['local_type'])
                if guessed_path:
                    content = f"[图片: {guessed_path}]" if r['local_type'] == 3 else f"[表情: {guessed_path}]"

            reply_ctx = extract_reply_context(raw_content, base_dir, chat_id)
            at_users = extract_at_users(dec(r['source']) if 'source' in r.keys() else '')
            if not content:
                continue
            ts = int(r['create_time'] or 0)
            row = {
                'time': datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else '',
                'local_id': int(r['local_id']),
                'sender_user': sender,
                'sender_display': display_for(sender, contact_map),
                'content': content,
                'local_type': int(r['local_type'] or 0),
            }
            if reply_ctx:
                if reply_ctx.get('quoted_user'):
                    reply_ctx['quoted_display'] = display_for(reply_ctx.get('quoted_user'), contact_map)
                row['reply_context'] = reply_ctx
            if at_users:
                row['at_users'] = at_users
            rows.append(row)
        con.close()
        if after_seq <= 0:
            rows = list(reversed(rows))
        return {
            'ok': True,
            'source_mtime': dbp.stat().st_mtime,
            'contact_map_mtime': resolve_contacts_raw_json().stat().st_mtime if resolve_contacts_raw_json().exists() else None,
            'rows': rows,
        }
    finally:
        try: os.unlink(tmp.name)
        except Exception: pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=os.environ.get('WECHAT_TAIL_DB_PATH', ''))
    ap.add_argument('--table', default=os.environ.get('WECHAT_TAIL_DB_TABLE', 'MSG'))
    ap.add_argument('--limit', type=int, default=32)
    ap.add_argument('--after-seq', type=int, default=0)
    ap.add_argument('--self-users', default='')
    ap.add_argument('--peer-users', default='')
    ap.add_argument('--self-label', default=DEFAULT_SELF_LABEL)
    ap.add_argument('--peer-label', default=DEFAULT_PEER_LABEL)
    ap.add_argument('--db-key', default=os.environ.get('WECHAT_TAIL_DB_KEY', ''))
    ap.add_argument('--target-chatroom', default=os.environ.get('WECHAT_TAIL_TARGET_CHATROOM', ''))
    args = ap.parse_args()
    self_users = [x for x in args.self_users.split(',') if x]
    peer_users = [x for x in args.peer_users.split(',') if x]
    try:
        out = tail(args.db, args.table, args.limit, args.after_seq, self_users, peer_users, args.self_label, args.peer_label, args.db_key, args.target_chatroom)
    except Exception as e:
        out = {'ok': False, 'error': repr(e), 'rows': []}
    print(json.dumps(out, ensure_ascii=False))

if __name__ == '__main__':
    main()
