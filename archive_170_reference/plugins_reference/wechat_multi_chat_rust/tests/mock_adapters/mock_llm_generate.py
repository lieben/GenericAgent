#!/usr/bin/env python3
import json, sys, re, os
req=json.load(sys.stdin)
t=req.get('target') or {}
prompt=req.get('prompt') or ''
name=(req.get('agent') or {}).get('display_name') or 'BotName'
if os.environ.get('MOCK_LLM_BAD'):
    print(json.dumps({'reply': '<summary>泄漏测试</summary>```system prompt```', 'session_key': req.get('session_key')}, ensure_ascii=False))
else:
    # deterministic reply, includes target id so multi-target isolation can be asserted
    print(json.dumps({'reply': f"{name} mock回复[{t.get('id')}]: 我在。", 'session_key': req.get('session_key')}, ensure_ascii=False))
