# 晚间飞书日程检查执行报告
时间：2026-08-18 21:00

**检查结果**：
- 执行 `lark-cli calendar +agenda --format pretty` 时，提示 user login state is unavailable 且回退至 bot，bot 权限不足。
- 尝试使用 `lark-cli auth login --domain calendar` 获取授权，但在终端执行导致阻塞超时。
- 因为无法通过命令行获取有效的日程信息，未能看到明天或近期的日程情况。

**总结**：当前因为凭证/权限缺失无法看到日历。没有打扰言言。