# 飞书晚间日程检查报告

- **执行时间**: 2026-07-08 21:00
- **任务状态**: 失败
- **失败原因**: `lark-cli` 提示需要用户登录 (`calendar_user_login_required`)，授权可能已过期。
- **原始报错**: 
  ```json
  {
    "ok": false,
    "identity": "bot",
    "error": {
      "type": "calendar_user_login_required",
      "message": "calendar commands require a valid user login by default; when no valid user login state is available, auto identity falls back to bot and may operate on the bot calendar instead of your own. Run `lark-cli auth login --domain calendar` for your calendar, or rerun with `--as bot` if bot identity is intentional.",
      "hint": "restore user login: `lark-cli auth login --domain calendar`\nintentional bot usage: rerun with `--as bot`"
    }
  }
  ```
- **结论**: 未能读取到日程信息。需要手动重新授权。