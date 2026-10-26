# lark_calendar_morning_check 执行报告

- 任务：lark_calendar_morning_check
- 执行时间：2026-07-27T09:01:27+08:00
- 报告路径：`/home/aimcafe/GA_Instances/xinghui-ga/sche_tasks/done/2026-07-27_0900_lark_calendar_morning_check.md`
- 操作边界：仅查看飞书日程；未修改日历；未主动打扰言言。

## 执行命令

```bash
/home/aimcafe/.local/bin/lark-cli calendar +agenda --format pretty
```

## 结果摘要

- status：**BLOCKED_AUTH_REQUIRED**
- 日程读取结果：**未能读取言言的用户日历**。
- 原因：`lark-cli` 当前没有有效用户登录态，自动退回 `bot` 身份；日历命令明确提示需要有效 user login，否则可能读取机器人日历而不是言言自己的日历。
- 我没有改用 `--as bot`，因为本任务目标是查看言言的飞书日程，机器人日历不等同于言言日历，使用它会产生误报。

## 今日/近期提醒判断

由于用户日历读取被认证状态阻断：

- 今天/近期是否有重要日程、比赛/出行/提醒事项：**无法从飞书日历确认**。
- 是否有言言需要提前准备或确认的事项：**无法从飞书日历确认**。
- 是否需要现在打扰言言：**不需要**。本次没有读到明确临近且重要的事项；按任务边界，不因认证失效打扰言言。

## 认证状态探测

随后执行：

```bash
/home/aimcafe/.local/bin/lark-cli auth status --verify
```

结果显示：

```text
{
  "appId": "cli_a96966b36278dcb1",
  "brand": "feishu",
  "defaultAs": "auto",
  "identity": "bot",
  "note": "Token does not exist or has been cleared. Only bot (tenant) identity is available. Re-login: lark-cli auth login",
  "userName": "lieben",
  "userOpenId": "ou_810ab066e96b6f50cd9b6021c832230a"
}
```

关键句：`Token does not exist or has been cleared. Only bot (tenant) identity is available. Re-login: lark-cli auth login`

## 原始 agenda 输出

```text
{
  "ok": false,
  "identity": "bot",
  "error": {
    "type": "calendar_user_login_required",
    "message": "calendar commands require a valid user login by default; when no valid user login state is available, auto identity falls back to bot and may operate on the bot calendar instead of your own. Run `lark-cli auth login --domain calendar` for your calendar, or rerun with `--as bot` if bot identity is intentional.",
    "hint": "restore user login: `lark-cli auth login --domain calendar`\nintentional bot usage: rerun with `--as bot`"
  },
  "_notice": {
    "update": {
      "current": "1.0.15",
      "latest": "1.0.76",
      "message": "lark-cli 1.0.76 available, current 1.0.15"
    }
  }
}
```

## 后续建议

若需要恢复晨晚飞书日程检查，需要言言在方便时重新完成：

```bash
/home/aimcafe/.local/bin/lark-cli auth login --domain calendar
```

或根据当前版本提示使用可用的登录命令恢复 user calendar 登录态。恢复前，飞书日程例行检查只能如实报告“用户日历未读到”。
