# Lark Calendar Morning Check Report
- Time: 2026-07-31 09:05
- Status: Failed
- Error Type: Permission Denied (App Scope Not Enabled)
- Detail: `lark-cli calendar +agenda` failed with error code 99991672. The Feishu App (cli_a96966b36278dcb1) lacks the required scope `calendar:calendar.event:read`. 
- Identity Used: bot (default)
- Note: User login token is missing or expired, and bot identity does not have permission to read calendar events.

## Summary
Could not retrieve agenda due to permission issues. Please check the Feishu Developer Console to enable the calendar read scope for the app.
