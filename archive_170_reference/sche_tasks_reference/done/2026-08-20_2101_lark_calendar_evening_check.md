# 飞书日程晚间检查报告
时间：2026-08-20 21:01

**执行结果：**
- 运行 `lark-cli calendar +agenda --format pretty`：提示 `calendar_user_login_required`（日历需要有效用户登录授权）。
- 尝试 `--as bot` 查询：返回应用权限未开启（App scope not enabled: `calendar:calendar.event:read`）。

**日程与提醒判断：**
- 因用户登录凭据失效且 Bot 权限未开，当前无法直接读取到日程数据。
- 遵循边界要求：例行查看不打扰言言，待后续日常交互时再视情况提示重新授权登录即可。
