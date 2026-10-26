#!/usr/bin/env python3
import json, sys
req=json.load(sys.stdin)
t=req.get('target') or {}
inject=req.get('inject') or {}
items=inject.get('items') or []
text=items[0].get('text','') if items else ''
# 返回固定正文，并把注入摘要放meta里供测试断言；reply本身不含内部包装。
print(json.dumps({
  'reply': '收到，我在。',
  'source': f"mock_main:{t.get('id','unknown')}",
  'session_key': req.get('session_key') or t.get('session_key'),
  'main_session_key': inject.get('main_session_key') or 'xinghui:main',
  'mock': True,
  'seen_inject_text': text
}, ensure_ascii=False))
