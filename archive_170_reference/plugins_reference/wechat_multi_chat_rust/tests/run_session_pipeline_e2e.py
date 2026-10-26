#!/usr/bin/env python3
import json
import pathlib
import subprocess
import shutil

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = ROOT / 'target' / 'debug' / 'wechat_multi_chat_rust'
CONFIG = ROOT / 'tests' / 'fixtures' / 'session_pipeline_config.json'

if not BIN.exists():
    subprocess.run(['/home/aimcafe/.cargo/bin/cargo', 'build'], cwd=ROOT, check=True)

state = ROOT / 'runtime' / 'state.json'
backup = ROOT / 'runtime' / 'state.json.session_pipeline_e2e.bak'
if state.exists():
    shutil.copy2(state, backup)
    state.unlink()
try:
    proc = subprocess.run([str(BIN), '--config', str(CONFIG), 'once', '--dry-run'], cwd=ROOT, text=True, capture_output=True, check=True)
finally:
    if backup.exists():
        if state.exists():
            state.unlink()
        shutil.move(str(backup), str(state))
data = json.loads(proc.stdout)
assert data['ok'] is True, data
reports = data['reports']
assert len(reports) == 1, reports
rep = reports[0]
assert rep['status'] == 'reply', rep
assert rep['target'] == 'session-e2e-group', rep
# 当前 Rust once 报告只公开 reply/send/status，不暴露内部 generated；
# session_pipeline 是否启用由专用 config 中 session_inject + main_reply mock 保证。
assert rep['reply'] == '收到，我在。', rep
assert rep['send']['dry_run'] is True and rep['send']['sent'] is False, rep['send']
assert 'summary' not in rep['reply'].lower(), rep['reply']
assert '[微信多聊注入' not in rep['reply'], rep['reply']
print('session_pipeline_e2e ok')
