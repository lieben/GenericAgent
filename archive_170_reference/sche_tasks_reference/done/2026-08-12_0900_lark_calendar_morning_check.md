# 飞书日程早晨检查 执行报告

## 执行结论
- **状态**：失败（权限不足）
- **错误信息**：`lark-cli` 提示当前 bot 身份（`cli_a96966b36278dcb1`）未开启 `calendar:calendar.event:read` 权限。
- **排查详情**：
    - 尝试以 `auto` 身份执行：提示需要用户登录（`calendar_user_login_required`）。
    - 尝试以 `bot` 身份执行：提示权限缺失 `required scope calendar:calendar.event:read`。
    - 检查 `auth status`：确认当前仅 bot token 可用，用户 login 已失效或未登录。
- **近期日程**：未能成功读取。

## 建议动作
- 言言如果有空，可能需要为 `cli_a96966b36278dcb1` 应用开启日历读取权限，或在物理机运行 `lark-cli auth login --domain calendar` 重新完成用户授权。

## 检查结论
- 未能获取日程，因此无法判断是否有重要事项。保持静默，不因此错误主动联系言言，仅作内部记录。
