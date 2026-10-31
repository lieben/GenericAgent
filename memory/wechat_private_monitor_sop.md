# 微信私聊监控（Windows 176 与多端适配规范）

## ⚠️ 运行宿主与调度铁律（最高优先级，严禁混淆违背）
- **运行宿主**：微信私聊监控运行在 Windows 176（`192.168.1.176`），不是 Mac！严禁在排查时扯到 Mac 微信，言言微信常驻运行在 Win176 上。
- **计划任务名称隔离铁律（致命冲突排查）**：
  - **私聊发送专用任务**：必须是 **`Xinghui-Private-Send`**！它执行 `C:\Users\Administrator\private_monitor\run_send.ps1`，读取 `private_monitor\send_task.json`。
  - **群聊发送任务**：是 `SendTestTask`，它执行 `group_monitor\run_send.ps1`，读取 `group_monitor\send_task.json`。
  - **历史血泪 Bug（2026-10）**：`private_monitor/wechat_windows_adapter.py` 曾错误地调用了群聊的 `SendTestTask`。导致私聊把待发内容写在 `private_monitor`，群聊任务却去 `group_monitor` 找文件，报 `Task file not found`；而私聊 adapter 一直等不到回执超时 30 秒卡死！此项已彻底纠正，严禁改回。
- **输入框物理聚焦与发送铁律（2026-10 实录已验证）**：
  - 微信 4.0 聊天窗口为 DirectUI 架构，**严禁使用 `PostMessage(WM_LBUTTONDOWN/UP)` 等虚拟窗口消息点击**！虚拟消息会被丢弃，导致光标根本没有落入输入框，后续粘贴完全落空，还会返回虚假成功。
  - **必须使用真实的物理光标点击**：`win32api.SetCursorPos((screen_x, screen_y))` + `win32api.mouse_event(MOUSEEVENTF_LEFTDOWN/UP)`，精准点击窗口底部向上 80px 的真实屏幕坐标聚焦。
  - **发送按键**：微信 4.0 在输入框获得真焦点后，直接按键盘 `VK_RETURN` (Enter) 即可完成上屏发送。
- **进程名铁律（2026-10 实录）**：Win176 安装的是**微信 4.0 架构重构版**，主程序进程名是 **`Weixin.exe`**，窗口类名是 **`WeChatMainWndForPC`**（独立窗口与主窗口均保留此通用类名），完全可以正常通过标题 `无言` 秒级激活！严禁妄称类名改变。

## 架构与核心流程
1. **运行宿主**：Windows 176（`192.168.1.176`）计划任务 `Xinghui-Private-Monitor`，常驻运行 `C:\Users\Administrator\private_monitor\wechat_xavier_private_monitor.py`。
2. **多端/跨平台发送桥接**：`wechat_windows_adapter.py`。
   - 当检测到目标窗口存在时，通过 `wechat_task_runner.py` 调度交互式任务执行剪贴板粘贴与键盘发送。
   - 发送脚本执行完毕后将结果序列化写入临时回执 JSON。
3. **回执规范与编码安全**：
   - Windows 环境下写入 JSON 文件严禁携带 UTF-8 BOM，或读取端必须使用 `encoding='utf-8-sig'` / `utf-8` 双兼容。
   - 禁止因回执解析异常导致误判发送失败，否则重试机制（默认3次）会重复触发大模型生成并连发多条回复。
4. **窗口定位机制**：
   - 严禁在独立私聊窗口中使用 `Ctrl+F` 搜索联系人，否则会造成焦点错乱离开消息输入框。
   - 直接通过物理匹配激活目标窗口（标题如 `无言`），点击窗口中心偏下位置（底部向上 80px）物理聚焦输入框，再进行剪切板物理粘贴发送，敲 Enter 上屏。

## 已验证保护：同步时禁止丢失
- 修改、移植或打包前先对照目标版本与 git 历史；只做局部补丁，禁止用旧副本整文件覆盖已修复版本。改发送适配不能删除生成阶段保护。
- 私聊生成异常必须捕获并计入 `fail_count`，刷新 `last_update` 等待 debounce；累计三次失败移除 pending 批次并记录 `generate_exception_max_retries`，禁止无限重试。空回复与发送失败的既有保护也必须保留。
- 发布/部署前必须运行分发仓 `wechat/private-monitor/test_generation_breaker.py`；覆盖生成异常熔断、debounce、失败后恢复、空回复、发送失败。仅语法通过不等于行为验证。
- **发送等待必须随气泡数放大**：`wechat_windows_adapter.py` 按段数计算 `min(45, 8 + (段数-1) × 1.8)` 秒（2026-10-21 实测每段约 1.0~1.8 秒，5 段需 9 秒，旧的固定 8 秒会误判超时并触发重发）。段数统计 `_count_segments()` 必须与 `wechat_task_runner.split_segments()` 规则一致；改任一处都要同步另一处并重跑隔离回执测试。
- 部署前备份并核对目标内容，测试通过后仅重启获准的服务；真实发送测试应单独确认，不得用进程存活冒充成功。

## 比对基准铁律（2026-10-21 言言纠正，两次误判的根因）
- **基准永远是线上正在运行的文件**，不是仓库副本、不是 temp 快照。先确认进程实际加载路径：
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select ProcessId,CommandLine`
  再用 `certutil -hashfile <该路径> MD5` 取真实哈希，与 GitHub 同路径文件哈希直接对比。
- **仓库存档（`wechat/win176/`）只用于回滚，不作事实来源**。存档与线上不一致时以线上为准，并把存档刷新为线上当前版本；旧档移入 `wechat/win176/backup/`，文件名带日期与旧 MD5。
- **动手前先做"当前时刻"的新备份**：线上原文件复制为 `*.bak_YYYYMMDD_HHMM` 并回读校验 MD5；严禁拿已有旧备份当"安全备份"。
- **计划任务拉起的文件同样要追到最终路径**：`schtasks /query /tn SendTestTask /fo LIST /v`（项名"要运行的任务"）→ `run_test.ps1` → `python.exe <脚本>`；`wechat_task_runner.py` 这类由计划任务执行的脚本，以其最终被调用的路径为准。
- **本地存档清单**（`wechat/win176/`，仅供回滚）：`wechat_xavier_private_monitor.py`、`wechat_task_runner.py`、`wechat_windows_adapter.py`；均按线上当前哈希刷新，旧档改名进 `backup/`。
- 实例：2026-10-21 用 10-20 20:02 的快照（`e2dd9473`）去比 GitHub（`e45b9f2e`），误判"GitHub 领先现网"，实际线上与 GitHub 逐字节相同。旧快照比线上落后 1910 字节、差 23 小时。

## 上下文与来源标注规范（2026-10-19 已验证）
- **历史由 GA 维护，脚本不得重复注入**：GA 自封装 API 已按 `chat_history.max_turns`（默认 30 轮）自行维护对话历史。私聊/群监控脚本**只发本轮新消息**，严禁再把「较早历史 / 历史背景」整段拼进发给 GA 的 prompt。
  - 历史教训：脚本曾把「30 条历史 + 新消息」整份当 user content 发给 GA，GA 原样落库 → 每条记录 99% 是重复旧历史（实测 8430/8495 字符），上下文平方级膨胀。
  - 私聊脚本 `xavier()`：content 只保留 `"【本轮新消息，按时间排序】"`，不再拼 `history_text`。
  - 群监控脚本 `xavier()`：prompt 中删除「历史背景（只作理解…）」段，只保留「本轮新消息」段。
- **来源暗号由服务侧写、GA 只提取**：微信私聊来源行由 Win176 私聊脚本生成，格式：
  `{time}【.跨次元微信私聊.】【来源微信私聊：{昵称}｜微信号：{talker}｜{对方/我方}】 {正文}`
  - 暗号 `【.跨次元微信私聊.】` **只由微信私聊服务写入**，GA 端仅用正则提取该暗号作为落库 source，**严禁在 GA 端硬编码**（否则等于 GA 伪造来源）。
  - 只有微信私聊互动才带此标记；群聊/其他渠道不得复用。
- **静默轮次不落库**：GA 对空回复或 `{"continue": false}` 的轮次不写入历史（`_run_api_chat_turn`）。这是设计行为，去掉脚本历史注入后，静默轮次不再出现在历史中，属预期。

## 强隔离铁律：群聊与私聊强隔离
- **物理与代码绝对隔离**：群聊监控（`wechat_group_monitor`）与私聊监控（`wechat_private_monitor`）属于两套独立业务。
- **改群聊绝不碰私聊**：任何时候修改群聊逻辑（Flash 参与门控、群聊识图、群聊@机制等），绝对不得修改、覆盖、重启或影响私聊相关的任何文件及进程。
- **公共适配层修改需回归两端**：若必须修改 `wechat_windows_adapter.py` 等共享模块，必须双向验证私聊与群聊，确保回执协议一致。
