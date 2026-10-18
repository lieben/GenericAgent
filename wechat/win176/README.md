# Win176 微信服务（固定副本）

> ⚠️ **运行宿主 = Windows 176（192.168.1.176），不是 Mac。**
> 本目录只是从 Win176 生产环境拉回的**只读固定副本**，用于版本对照与防回退。
> **实际运行的文件在 Win176 上**，改代码请 SSH 到 `win176` 改生产文件，再回同步本目录。

## 这些文件是什么

| 文件 | Win176 生产路径 | 说明 |
| --- | --- | --- |
| `wechat_xavier_private_monitor.py` | `C:\Users\Administrator\private_monitor\wechat_xavier_private_monitor.py` | 微信私聊监控主脚本（计划任务 `Xinghui-Private-Monitor`） |
| `wechat_xavier_group_monitor.py` | `C:\Users\Administrator\group_monitor\wechat_xavier_group_monitor.py` | 微信群监控主脚本（计划任务 `Xinghui-Group-Monitor`） |

Mac 侧只有 GA 大脑（自封装 API，`:1016`）。私聊/群监控脚本、WeFlow、发送适配全部在 Win176。

## 固定快照（2026-10-19）

- `wechat_xavier_private_monitor.py` SHA256 = `C302F236683BACC7108DA3B77C9F7D4A605C9F0A88BD50DF9153E464F062D990`
- `wechat_xavier_group_monitor.py`   SHA256 = `C2564F9DFE2418F01A4C936ECA12CED716CD50641CB13655FE809242D2403724`

校验命令（在 Win176 上）：
```powershell
Get-FileHash C:\Users\Administrator\private_monitor\wechat_xavier_private_monitor.py -Algorithm SHA256
Get-FileHash C:\Users\Administrator\group_monitor\wechat_xavier_group_monitor.py -Algorithm SHA256
```
若与上表不一致，说明 Win176 生产版已改动，需重新同步本目录并更新 SHA。

## 本快照包含的已验证修复

1. **去掉重复历史注入**：私聊/群监控脚本只发「本轮新消息」，不再把「较早历史 / 历史背景」整段拼进发给 GA 的 prompt。历史由 GA 按 `max_turns`（默认 30 轮）自行维护。
   - 私聊 `xavier()`：content 只保留 `"【本轮新消息，按时间排序】"`。
   - 群监控 `xavier()`：prompt 删除「历史背景（只作理解…）」段。
2. **来源暗号由服务侧写**：私聊来源行带 `【.跨次元微信私聊.】`，GA 端只提取、不硬编码。

详见 `memory/wechat_private_monitor_sop.md` 与 `memory/ga_api_xavier_sop.md`。

## 部署铁律

- 禁止用旧副本整文件覆盖 Win176 已修复版本；只做局部补丁。
- 改完先 `python -m py_compile`，再重启对应计划任务。
- 私聊与群聊强隔离：改群聊绝不碰私聊文件/进程，反之亦然。
