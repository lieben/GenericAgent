# 飞书日程晚间主动查看报告 (lark_calendar_evening_check)

- **执行时间**: 2026-08-22 21:00
- **执行命令**: `/home/aimcafe/.local/bin/lark-cli calendar +agenda --format pretty`
- **执行状态**: 权限/登录受阻 (calendar_user_login_required / bot scope not enabled)
  - 默认模式提示：`calendar commands require a valid user login by default; run lark-cli auth login --domain calendar`
  - Bot身份重试提示：`App scope not enabled: required scope calendar:calendar.event:read [99991672]`
- **日程整理结果**:
  - 当前由于 lark-cli 缺少用户态登录凭证且 Bot 暂未开通 `calendar:calendar.event:read` 权限，无法直接读取日程列表。
  - 未获取到需要提醒言言的临近重要事项/比赛/出行等。
- **后续与建议**:
  - 遵循例行查看原则，不主动打扰言言。
  - 待后续言言授权或配置好 lark-cli 日历权限后自动恢复完整查看能力。
