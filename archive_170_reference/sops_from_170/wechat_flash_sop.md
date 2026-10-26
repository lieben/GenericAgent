# WeChat Flash SOP

## 触发场景
- 微信群监听进程还在，但某群（如 `正常人类聚集地`）加入聊天后长期只看到 `fresh=0`，疑似 flash 没判断。

## 关键判断
- 先查 `wechat_multi_chat_rust` 进程与 `logs/events.jsonl`：如果持续 poll 但 `fresh=0`，不要先怀疑配置关闭。
- 查 xwechat message DB/WAL/SHM 的 mtime：如果停在很久以前，常见根因是 WeChat GUI 没在主界面，导致数据库不再落库。
- 枚举窗口/进程确认微信 GUI；若没有主窗口，启动 `/usr/bin/wechat`。
- 若持续 `fresh>0` 却只有 `flash_not_configured_or_unavailable` / `low_freq_flash_unavailable`：先核对 `config/config.json` 的 `flash_decision.model`，再确认 loop 是否已重启吃到新配置；flash 不可用时不会回落主会话。

## Flash 模型（已验证）
- 正确模型名：`gemini-3-flash-preview`（全局与 `group-normal-human` 等同此名；`llm_no` 可 null，由 adapter 按模型名解析）。
- 旧名 `code-gemini-3-flash-preview` 已废弃：当前 llm 列表无此名，写进配置/记忆会解析失败。
- `wechat_multi_chat_rust` loop **启动时读配置，不热加载**；改 `flash_decision` 后需重启 `ga-wechat-group-monitor.service`（`start/stop_wechat_services.sh`）才生效。
- 验收：`logs/events.jsonl` 的 decision/`flash_meta.model` 应为 `gemini-3-flash-preview`，且出现 `flash_model_skip`/`flash_model_send`，而非 unavailable。

## 已验证恢复路径
- `/usr/bin/wechat` 可启动桌面微信。
- 若出现小窗口标题 `微信`，本地 OCR 可能读到 `Xavier / 进入微信 / 切换账号 / 仅传输文件`。
- 先激活窗口，再点击 OCR 确认的 `进入微信`；不要盲点。
- 进入主界面后，DB WAL/SHM mtime 应更新，`events.jsonl` 应出现非零 `fresh`、`flash_decide_start/done`。

## 发图到指定微信群（2026-08-15 起唯一简化流程）
- 前置：言言已经在当前对话明确确认官网当前可见的最新日报图 OK；未确认不得发送。
- 从官网当前确认的图片直接执行“复制图片”，让图片进入系统剪贴板；不下载、不保存、不经过 Nautilus。
- **前台与坐标**：先 `wmctrl -s 0` 切到 Chrome 所在工作区，再 `wmctrl -ia <Chrome>`，以活动窗口标题确认 Chrome 在前台；图像右键坐标必须根据当前截图/DOM重新确认，禁止沿用旧坐标。
- **复制方式（唯一可行）**：必须右键图片→菜单点“复制图片”；Ctrl+C 无效。Chrome 右键菜单是独立弹层窗，xwd -root 全屏截图抓不到。正确做法：右键后 xwininfo -root -tree 找附近约355x320无名小窗，import -window 单独截图并 OCR；仅在 OCR 明确识别“复制图片”后，按菜单窗原点+局部坐标物理点击，禁止 Home/Down 猜菜单。
- 枚举窗口，激活标题**完整匹配**的目标微信窗口「正常人类聚集地」；不改变窗口尺寸、不切换到其他群聊。激活后必须在发送前对该窗口本身截图/OCR，确认聊天区顶部完整群标题和底部输入框均可见；`wmctrl` 的 active 状态、窗口列表存在或标题相似的「微信」主窗均不能替代此确认。输入框位置按当前窗口/客户区确认，不沿用旧窗口外框坐标。
- **2026-08-21 成功路径**：先完成上述群窗前置与截图确认，再回 Chrome 右键当前已确认海报→等待 1 秒→对独立约 355×320 无名菜单窗口 OCR 确认“复制图片”→按该菜单窗口原点加局部坐标点击；随后以 `xdotool windowactivate --sync <完整群标题窗口ID>` 回到刚验证的群窗，使用该窗口客户区相对坐标点击已确认的输入区，`Ctrl+V`、短等待、`Enter` 各一次即停止。
- 若目标群窗口不存在，按正常微信方式从会话列表双击打开目标群，再以聊天区顶部完整标题确认；随后继续发送。
- 直接 `Ctrl+V`，短等待，`Enter` 发送；发送动作结束即停止。
- 禁止：读取/回读剪贴板、TARGETS/uri-list检查、拖图、粘贴预览检查、发送后截图/OCR/vision验证、等待气泡验收、下载流程、Nautilus流程、自动发送适配器、手工 xclip owner、修改微信通讯管线。
- 失败时只记录实际错误并报告；不得在无新信息时重复发送。言言已确认本次按上述闭环发送成功。

## Linux GUI 自主打开（2026-08-09 实测）
- 先按群聊排他规范核验服务，再启动：`/usr/bin/wechat`；将窗口放到独立工作区（`wmctrl -ir <窗口ID> -t <目标桌面索引>`），避免占用 Obsidian/浏览器所在桌面。
- 若窗口为约 `280x380` 的登录小窗，先截取**微信窗口本身**；实测绿色“进入微信”按钮区域约为窗口内 `(50,270)-(230,306)`，但坐标仅作候选，仍需对当前截图复核。点击后必须重新枚举窗口：变为约 `722x959` 主窗口才算登录成功。
- Linux 上本机 `ljqCtrl.py` 依赖 `win32api`，不可用时不要安装无关 Windows 包；使用 `xdotool`，操作前 `wmctrl -ia <窗口ID>` 激活。
- **关键避坑**：窗口重建后 ID/几何可能变化；枚举窗口只用于确认当前可操作句柄/映射状态，不用于寻找或切换群聊。不要把截图坐标直接叠加旧的窗口外框原点；确需点击时，用客户区相对坐标：`xdotool mousemove --window <十进制窗口ID> <x> <y>`，再用 `getmouselocation` 确认命中该目标窗口。
- 恢复目标群聊窗口时，先在当前窗口截图中识别目标会话行；按正常群聊方式**双击打开相应群聊窗口**，随后立即重截，并以新窗口/聊天区域顶部完整群标题为准验收，不能只看左侧列表。禁止再使用“单击整行中心”作为打开目标群的方法。

## 操作约束
- 本节只适用于登录、恢复群窗口等前置操作；不覆盖言言确认后的末端 `Ctrl+V`→`Enter` 发送。末端发送严格禁止截图/OCR/vision验收。
- 遵守 `ljqCtrl_sop`/`vision_sop`：前置阶段先枚举并激活目标窗口；当前 Ubuntu 可按已验证的 xwd+convert 主路径全屏截图，精确点击仍优先窗口/局部截图与本地 OCR。
- 前置点击前必须确认按钮文字/坐标；涉及扫码或手机确认时请求言言处理。
