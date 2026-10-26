#!/usr/bin/env python3
import json, sys
req=json.load(sys.stdin)
t=req.get('target') or {}
print(json.dumps({'sent': True, 'mock': True, 'target': t.get('id'), 'wechat_title': t.get('wechat_title'), 'text': req.get('text')}, ensure_ascii=False))
