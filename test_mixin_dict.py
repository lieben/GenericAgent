import sys, time
sys.path.insert(0, '/Users/wuchong/Library/Application Support/GenericAgent/runtime/app')

import agentmain
GA = getattr(agentmain, "GenericAgent")
agent = GA()
backend = agent.llmclient.backend

t0 = time.time()
print("Direct asking backend ask with dict...")
try:
    gen = backend.ask({"role": "user", "content": "1+1=?"})
    for chunk in gen:
        print("Chunk:", chunk)
        break
    print("Success in", round(time.time() - t0, 2), "s")
except Exception as e:
    import traceback
    print("Failed in", round(time.time() - t0, 2), "s:")
    traceback.print_exc()

