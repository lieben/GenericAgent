# 晚间飞书日程检查 (2026-08-25 21:02)

**检查状态**：失败，当前账号的日历授权已失效 (`calendar_user_login_required`)。
**详情**：
调用 `lark-cli calendar +agenda` 时，接口提示需要重新进行 OAuth 用户认证（`https://accounts.feishu.cn/oauth/v1/device/verify...`）。
考虑到不打扰言言且无法通过自动脚本独立完成浏览器授权，本次检查已中止。

**行动建议**：
1. 暂无获取最新日程的信息。
2. 稍后在合适的聊天时机或者后台任务时，可以提醒言言重新为当前实例的 `lark-cli` 赋予日历权限。
