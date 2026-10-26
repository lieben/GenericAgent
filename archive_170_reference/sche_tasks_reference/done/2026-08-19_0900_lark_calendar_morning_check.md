# 早上飞书日程查看执行报告
时间：2026-08-19 09:00

- **执行结果**：执行 `lark-cli calendar +agenda --format pretty` 失败。
- **日程情况**：未能获取。
- **失败原因**：提示 `calendar_user_login_required`。当前飞书日历仍处于未授权/登录失效状态，需要使用 `lark-cli auth login --domain calendar` 重新登录才能获取用户日历。由于这是例行检查，且遵循不打扰的原则，未强行要求言言马上扫码，仅在此如实记录未授权状态。