#!/usr/bin/env python3
import json, sys
from datetime import datetime
req=json.load(sys.stdin)
agent=req.get('agent') or {}
t=req.get('target') or {}
msgs=req.get('messages') if isinstance(req.get('messages'), list) else []
main_key=(
    (t.get('extra') or {}).get('main_session_key')
    or t.get('session_key')
    or agent.get('session_key')
    or agent.get('main_session_key')
    or 'xinghui:main'
)
title=t.get('wechat_title') or t.get('name') or t.get('id') or '微信会话'
kind=t.get('kind') or 'chat'
lines=[f"微信{'群聊' if kind=='group' else '私聊'}「{title}」刚刚有新消息："]
for m in msgs:
    text=str(m.get('text') or '').strip()
    if not text:
        continue
    sender='我' if m.get('is_self') else (m.get('sender') or '对方')
    ts=m.get('ts') or datetime.now().isoformat(timespec='seconds')
    lines.append(f"- {ts} {sender}: {text}")
lines.append('请根据配置身份自然判断是否回应；如果回应，只给出将发送到微信的正文，不要解释内部判断。')
body='\n'.join(lines)
print(json.dumps({
  'injected': True,
  'main_session_key': main_key,
  'source': f"multi_chat:{t.get('id','unknown')}:inject",
  'items': [{'role':'user','text':body,'sender': msgs[-1].get('sender') if msgs else '__multi_chat_timeline__','target_id': t.get('id'),'kind': kind,'wechat_title': title}],
  'context_file': '/tmp/mock_agent_context_xinghui.json',
  'mock': True
}, ensure_ascii=False))
