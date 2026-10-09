from typing import Any
import ast,json,re,threading,tempfile,os,time
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace as NS
s=(Path(__file__).resolve().parents[2]/'ga_api_server.py').read_text();tree=ast.parse(s)
names={'strip_final_info_marker','_clean_api_response','_extract_platform_marker','_dialogue_text','_clean_chat_reply','_dialogue_record','_load_api_dialogue','_api_max_turns','_api_chat_history','_save_api_dialogue','_run_api_chat_turn','_chat_request_instructions'}
g=globals();exec(compile(ast.Module(body=[n for n in tree.body if (isinstance(n,ast.FunctionDef) and n.name in names) or (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_FINAL_INFO_RE' for t in n.targets))],type_ignores=[]),'api_functions','exec'),g)
CONFIG={'chat_history':{'max_turns':2}};api_lock=threading.RLock()
with tempfile.TemporaryDirectory() as td:
 API_CHAT_FILE=Path(td)/'chat.json'
 _session_history_from_disk=lambda: [{'role':'user','content':'【第三方平台指令】OLD【用户消息】[2026-10-09 12:00:00] 【.test.】hello'},{'role':'assistant','content':[{'type':'tool_use','id':'1'}]},{'role':'user','content':[{'type':'tool_result','tool_use_id':'1'}]},{'role':'assistant','content':'<summary>internal</summary>{"content":"answer"}'},{'role':'user','content':'Long user prompt saved to x'}]
 records=_load_api_dialogue();assert len(records)==2;assert records[0]['source']=='【.test.】';assert records[1]['content']=='answer';assert records[1]['timestamp'] is None
 assert len(_api_chat_history(records*4))==4
 api_agent=NS(is_running=False,extra_sys_prompts=['BASE'],llmclient=NS(backend=NS(history=[])))
 def _run_api_turn_native(query,attachments=None):
  assert len(api_agent.extra_sys_prompts)==3 and api_agent.extra_sys_prompts[0]=='BASE' and api_agent.extra_sys_prompts[-1]=='【API 当轮特殊指令及召回内容，仅本轮有效】\nSPECIAL'
  assert attachments=={'file':'unchanged'}
  api_agent.llmclient.backend.history.append({'role':'assistant','content':[{'type':'tool_use','id':'native'}]})
  return '<summary>snapshot</summary>{"content":"final"}'
 assert _run_api_chat_turn('[2026-10-10 00:00:00] 【.test.】new','SPECIAL',{'file':'unchanged'})=='final'
 assert api_agent.extra_sys_prompts==['BASE'];saved=json.loads(API_CHAT_FILE.read_text())['messages'];assert len(saved)==4
 assert saved[-1]['source']=='【.test.】' and saved[-1]['timestamp'];assert 'SPECIAL' not in str(saved) and '<summary>' not in str(saved)
 assert 'tool_use' not in str(api_agent.llmclient.backend.history)
 def fail(*a,**k): raise RuntimeError('test failure')
 _run_api_turn_native=fail
 try:_run_api_chat_turn('failure','SPECIAL')
 except RuntimeError:pass
 assert api_agent.extra_sys_prompts==['BASE'];assert len(json.loads(API_CHAT_FILE.read_text())['messages'])==4
_text_content=lambda x:x or ''
r=NS(instructions='',system='',developer='',messages=[NS(role='system',content='【.test.】one'),NS(role='developer',content='two'),NS(role='system',content='three')]);v,m=_chat_request_instructions(r);assert all(x in v for x in ['one','two','three']);assert m=='【.test.】'
print('PASS: migration, tool exclusion, window, metadata, per-turn PR, attachment passthrough, reply cleaning, rollback, all system/developer')

assert _clean_chat_reply('[时间未知（旧记录）] 【.test.】 你好')=='你好'
assert _clean_chat_reply('[2026-10-10T12:30:00+08:00] 【.test.】 你好')=='你好'
assert _clean_chat_reply('正文里的 [2026-10-10 12:30:00] 保留')=='正文里的 [2026-10-10 12:30:00] 保留'
assert _clean_chat_reply('[FILE:/tmp/a]')=='[FILE:/tmp/a]'
print('PASS: metadata headers removed; prose dates and FILE preserved')
