# 飞书晚间日程主动查看报告 (2026-09-02 21:01)

- **检查时间**: 2026-09-02 21:01
- **检查命令**: `/home/aimcafe/.local/bin/lark-cli calendar +agenda --format pretty`
- **执行结果**:
  - 用户登录态不可用（`calendar_user_login_required`），回退至 bot 模式；
  - Bot 身份调用缺少应用权限 `calendar:calendar.event:read` (Code: 99991672)。
- **日程概况**:
  - 当前由于权限/登录态限制，未能成功拉取远端日程。
  - 未看到需要紧急提醒言言的临近日程事项。
- **后续提示**: 需在飞书开放平台为应用补齐日历读取权限或完成用户态认证登录。报告已如实归档。