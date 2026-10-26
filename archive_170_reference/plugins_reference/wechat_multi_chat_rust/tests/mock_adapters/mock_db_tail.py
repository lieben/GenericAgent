#!/usr/bin/env python3
import json, sys
req=json.load(sys.stdin)
t=req.get('target') or {}
msgs=t.get('mock_messages') or []
# simulate cursor: only emit seq greater than cursor if numeric
cursor=str(req.get('cursor') or '')
after=int(cursor) if cursor.isdigit() else 0
out=[]
for m in msgs:
    seq=int(m.get('seq') or 0)
    if seq>after:
        mm=dict(m)
        mm.setdefault('target_id', t.get('id'))
        mm.setdefault('target_identity', t.get('db_identity') or t.get('wechat_title') or t.get('id'))
        mm.setdefault('id', f"{t.get('id')}:{seq}")
        mm.setdefault('ts', f"2026-05-09 03:{seq:02d}:00")
        mm.setdefault('is_self', False)
        out.append(mm)
print(json.dumps({'messages': out}, ensure_ascii=False))
