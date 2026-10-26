# 飞书日程晨检执行报告 (2026-08-16 09:00)

<summary>飞书登录态失效，无法读取个人日程，晨检失败。</summary>

### 1. 执行结果
- **状态**：失败 (ERROR)
- **原因**：`lark-cli` 检测到用户登录态失效 (`calendar_user_login_required`)。由于当前是定时任务运行环境，无法进行交互式登录 (`lark-cli auth login`)。
- **错误详情**：
    ```json
    {
      "ok": false,
      "identity": "bot",
      "error": {
        "type": "calendar_user_login_required",
        "message": "calendar commands require a valid user login by default..."
      }
    }
    ```

### 2. 日程整理
- **今日日程**：无法读取。
- **近期事项**：无法读取。
- **言言需准备事项**：未知。

### 3. 后续处理
- **WxPusher/飞书提醒**：未触发。
- **建议**：言言在方便时，请在终端手动运行一次 `/home/aimcafe/.local/bin/lark-cli auth login --domain calendar` 以恢复登录态。

---
*Status: Failure due to authentication expiration.*
