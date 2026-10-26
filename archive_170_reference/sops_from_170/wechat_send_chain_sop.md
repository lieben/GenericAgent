# wechat 群聊发送链 (plugins/wechat_multi_chat_rust)

下次群聊没反应 / 静默断发，先看本文件，按下面顺序查。不要先猜模型、不要杀微信/gnome-shell。

## 优先排查 (2026-09-07 验证)
1. 看 `plugins/wechat_multi_chat_rust/logs/events.jsonl` 的 `data.error`(完整 adapter stderr)。`systemd.log` 只有 loop 状态；flash SKIP 是正常行为。
2. 症状若是 **generate 成功但 send 失败**（`Authorization required` / `Cannot open display` / `target window not found` / `clipboard verify failed`）：
   屏幕搬家了，不是监控死了。GNOME/Xwayland 会在 `:0`/`:1` 间搬家，mutter 轮转 `.mutter-Xwaylandauth.*`。
   以 `/proc/<wechat-pid>/environ` 为准，不要只看 unit 文件。adapter 必须 `x_env()` 调 wmctrl **和** xclip；drop-in `50-display-current.conf` 应 `UnsetEnvironment` DISPLAY/WECHAT_DISPLAY/XAUTHORITY，禁止硬编码。
3. 仍失败再查：vendor xclip 是否还在、wmshim PATH、窗口标题是否匹配。零副作用验证：preflight(`preflight_only:true`) / xclip 写读 / db_tail。
4. 失败后 cursor 已前进，**漏发默认不补发**，除非言言明确要求。

服务: user unit `ga-wechat-group-monitor`(Restart=always); drop-in 目录:
`~/.config/systemd/user/ga-wechat-group-monitor.service.d/`

## 发送链依赖 (adapters/rust_wechat_send.py)
- **DISPLAY 必须跟微信 GUI 同屏**: GNOME/Xwayland 会在 `:0`/`:1` 间搬家，mutter 还会轮转
  `.mutter-Xwaylandauth.*`。硬编码 drop-in 会过期（generate 成功，wmctrl 报
  `Authorization required` / `Cannot open display` / `target window not found`）。
  2026-09-07: drop-in `50-display-current.conf` 改为 `UnsetEnvironment=DISPLAY`
  `WECHAT_DISPLAY` `XAUTHORITY`；adapter 用 `adapters/x11_display.py` 探测
  `/proc/<wechat-pid>/environ` 的活屏。核环境以进程 environ 为准，不要只看 unit 文件。
- **xclip 走硬编码路径**(不在PATH): `plugins/wechat_multi_chat_rust/vendor/xclip/xclip/usr/bin/xclip`;
  缺失 → 生成成功但发送崩在 set_clipboard(rc=1, "portable xclip not found")
- wmctrl/xdotool 用于激活检查: gnome-shell WM 挂死时 `_NET_ACTIVE_WINDOW` 读不到 →
  垫片 `~/.wmshim/bin`(wmctrl 记 hint, xdotool getactivewindow 答 hint),
  经 drop-in `40-wmshim-path.conf` 注入服务 PATH
- 回显: vendor/wechat_live_tail/live_wechat_tail.py + 同目录隐藏 `.venv_wechatdb`(ls 要带 -a)
- 窗口按聊天标题逐条解析(群→群窗口/私→私窗口), 不串窗

## 排障
- **真实错误在 `logs/events.jsonl` 的 data.error**(adapter 完整 stderr); systemd.log 只有 loop 状态
- 重复 @/回复合并时，不要只信出站 event：用 DB tail 核对微信真实落库文本；常见链路是发送按钮点空→旧文本残留→下轮粘贴后合并
- 发送保护基线: 窗口尺寸安全且完全在屏内；**激活顶层窗口不等于激活编辑器**，必须像私聊链一样用 `xdotool mousemove --window <wid> <相对坐标> click 1` 点击输入框并再次验证目标标题，再清空/粘贴/Enter；逐步检查 xdotool 返回码；普通文字 DB echo 未确认必须报 adapter_error，不能记 sent
- 当前运行闭环看 `runtime/state.json`、`logs/events.jsonl`、`runtime_v2/systemd.log`; `runtime/rust_loop_health.json` 可能是旧机制遗留，须先核时间再引用
- flash SKIP 是正常行为(言言确认), 不是故障; 看 SEND 之后的 send 事件
- 回滚后必查: 备份 `backups/*/tracked_files` 与当前 diff, vendor 依赖/隐藏 venv 会被官方 overlay 回滚删掉
  (8/27 19:04 回滚即删掉 vendor/xclip 导致全链静默断发)
- 微信是 gnome-shell 子进程: 杀 shell = 微信掉线+重新扫码; 治本(重启 shell)需言言确认
- generate 成功但 send 报 `Cannot open display` / `target window not found` /
  `clipboard verify failed` / `Authorization required`:
  先核服务与微信进程的 DISPLAY(unit 会被 drop-in 覆盖，以 `/proc/<wechat-pid>/environ` 为准)；
  adapter 必须用 `x_env()` 调 wmctrl/xclip，禁止硬编码 `:0`/`:1`/`:10`。失败后 cursor 已前进，不会自动重发。
  漏发默认不补发，除非言言明确要求。
- 验证套路: 垫片 preflight(`preflight_only:true`) / xclip 写读回路 / db_tail 请求, 均零副作用
