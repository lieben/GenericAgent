import urllib.request, json, time

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
status = json.loads(opener.open('http://127.0.0.1:14168/status').read().decode())
active_id = status.get('activeSessionId')
print(f"Active Session ID: {active_id}")

# 看看该会话的 agent 是怎么创建的，或者我们在 bridge 进程里把当前卡住的线程堆栈抓出来
# 刚刚 lldb 抓到了 6 个线程，其中 Thread 3, 4, 5, 6 都在等锁！
# 到底是谁持有了锁？
# Thread 1 是主线程 (kevent 监听网络)
# Thread 2 是什么？
