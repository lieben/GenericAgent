# Win176 微信服务（固定副本）

> ⚠️ **运行宿主 = Windows 176（192.168.1.176），不是 Mac。**
> 本目录只是从 Win176 生产环境拉回的**只读固定副本**，用于版本对照与防回退。
> **实际运行的文件在 Win176 上**，改代码请 SSH 到 `win176` 改生产文件，再回同步本目录。

## 这些文件是什么

| 文件 | Win176 生产路径 | 说明 |
| --- | --- | --- |
| `wechat_xavier_private_monitor.py` | `C:\Users\Administrator\private_monitor\wechat_xavier_private_monitor.py` | 微信私聊监控主脚本（计划任务 `Xinghui-Private-Monitor`） |
| `wechat_task_runner.py` | `C:\Users\Administrator\private_monitor\wechat_task_runner.py` | 发送执行脚本（计划任务 `SendTestTask`），负责分气泡粘贴发送 |
| `wechat_windows_adapter.py` | `C:\Users\Administrator\private_monitor\wechat_windows_adapter.py` | 发送适配器，调度 runner 并按气泡数等待回执 |
| `wechat_xavier_group_monitor.py` | `C:\Users\Administrator\group_monitor\wechat_xavier_group_monitor.py` | 微信群监控主脚本（计划任务 `Xinghui-Group-Monitor`） |

Mac 侧只有 GA 大脑（自封装 API，`:1016`）。私聊/群监控脚本、WeFlow、发送适配全部在 Win176。

## 固定快照（2026-10-21）

- `wechat_xavier_private_monitor.py` SHA256 = `8729B1BCCFA9B20C4EECA12CD5CCC842E0C4897D12C28EEA035EA2AC11F819AD`
- `wechat_task_runner.py`          SHA256 = `C448C2EF6D34465D4BAC9F3363E963B23E6239940CE11AF6ED3A4034BA9F54D8`
- `wechat_windows_adapter.py`      SHA256 = `1FB14C5614FBEB48CB9888227ACA0EAE781F3DD910612909273651FE76CAAF1B`
- `wechat_xavier_group_monitor.py` SHA256 = `F12494D184AABE25DA935F74089C9DCA75E949DB75519D44B03BD56FDD9A3A67`（2026-10-21，群聊句末句号规范 + 双保险清理，已部署并重启 `Xinghui-Group-Monitor`）

校验命令（在 Win176 上）：
```powershell
Get-FileHash C:\Users\Administrator\private_monitor\wechat_xavier_private_monitor.py -Algorithm SHA256
Get-FileHash C:\Users\Administrator\private_monitor\wechat_task_runner.py -Algorithm SHA256
Get-FileHash C:\Users\Administrator\private_monitor\wechat_windows_adapter.py -Algorithm SHA256
Get-FileHash C:\Users\Administrator\group_monitor\wechat_xavier_group_monitor.py -Algorithm SHA256
```
若与上表不一致，说明 Win176 生产版已改动，需重新同步本目录并更新 SHA。

## 本快照包含的已验证修复

1. **去掉重复历史注入**：私聊/群监控脚本只发「本轮新消息」，不再把「较早历史 / 历史背景」整段拼进发给 GA 的 prompt。历史由 GA 按 `max_turns`（默认 30 轮）自行维护。
   - 私聊 `xavier()`：content 只保留 `"【本轮新消息，按时间排序】"`。
   - 群监控 `xavier()`：用户正文以 `【新消息】` 开头，只含本轮新消息；群规则与人设作为临时 system instruction，不写入历史。
2. **群聊图片改为原生多模态**：删除脚本侧 Flash 看图/`image_captions`/`画面：…` 文本；高频或言言发言时把原图作为 `image_url` 块交给 GA，低频仍只保留 `[图片]`。
3. **群聊上下文行修复**：每条消息压成单行并保留完整正文/引用，按时间正序；避免 GA 行解析把消息截断或把正文尾部误当成新消息。
4. **来源暗号由服务侧写**：私聊来源行带 `【.跨次元微信私聊.】`，GA 端只提取、不硬编码。
5. **分气泡发送**：`wechat_task_runner.py` 按空行（无空行时按单换行）拆段，逐段粘贴，段间 0.45 秒；`send_result.json` 的 `segments` 记录实际气泡数。
6. **发送等待按段数放大**：`wechat_windows_adapter.py` 由固定 8 秒改为 `min(45, 8 + (段数-1) × 1.8)` 秒，避免 5 段以上被误判超时而重发。
7. **群聊句末不带句号**：`DEFAULT_GROUP_SYSTEM_PROMPT` 增加「像微信随手回复、短句收尾、每条末尾不带句号（与私聊规范一致）」；`strip_trailing_period()` 在 Xavier 解析 content 处和 `physical_send()` 发送前双重清理末尾「。」与单个/连续英文句点，保留省略号「……」和「...」；按行处理以匹配分气泡发送。

详见 `memory/wechat_private_monitor_sop.md` 与 `memory/ga_api_xavier_sop.md`。

## 部署铁律

- 禁止用旧副本整文件覆盖 Win176 已修复版本；只做局部补丁。
- 改完先 `python -m py_compile`，再重启对应计划任务。
- 私聊与群聊强隔离：改群聊绝不碰私聊文件/进程，反之亦然。
