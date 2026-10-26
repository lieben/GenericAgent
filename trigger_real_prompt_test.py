import urllib.request, json, time

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 1. 创建会话
res = opener.open(urllib.request.Request('http://127.0.0.1:14168/session/new', data=b'{}', headers={'Content-Type': 'application/json'}, method='POST'))
sid = json.loads(res.read().decode())['session']['id']
print("Created session:", sid)

# 2. 发送消息 "在吗？"
payload = json.dumps({"prompt": "在吗？"}).encode()
req = urllib.request.Request(f'http://127.0.0.1:14168/session/{sid}/prompt', data=payload, headers={'Content-Type': 'application/json'}, method='POST')
print("Submitting prompt...")
resp = json.loads(opener.open(req).read().decode())
print("Submit response:", resp)

# 3. 轮询监控其执行全过程
t0 = time.time()
for _ in range(30):
    time.sleep(1)
    s = json.loads(opener.open(f'http://127.0.0.1:14168/session/{sid}').read().decode())['session']
    status = s.get('status')
    partial = s.get('partial')
    msgs = s.get('messages', [])
    elapsed = round(time.time() - t0, 1)
    part_content = partial.get('content') if partial else None
    print(f"[{elapsed}s] status={status}, msgs={len(msgs)}, partial={repr(str(part_content)[:60])}")
    if status in ['idle', 'cancelled', 'error']:
        if msgs:
            print("\nFinal message role:", msgs[-1].get('role'))
            print("Final message content:\n", repr(msgs[-1].get('content'))[:200])
        break

