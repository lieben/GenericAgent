# 飞书日程早间查看报告 (2026-09-10 09:01)

## 1. 检查概况
- **执行命令**：`/home/aimcafe/.local/bin/lark-cli calendar +agenda --format pretty`
- **检查状态**：执行失败 / 需要用户登录授权 (`calendar_user_login_required`)

## 2. 详细信息
- **错误详情**：
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
- **日程提取**：由于用户日历授权凭证失效，未能读取到言言的个人日历事件。
- **提醒事项**：当前未看到需要提醒的日程。

## 3. 边界与说明
- 例行检查，未修改任何日程。
- 登录态失效不打扰言言，后续待言言方便时按提示重新登录授权即可。
