# 群聊记忆处理 SOP

## 目录结构
`/home/aimcafe/ds memory/群聊记忆/`
├── `[群名].md`       # 长期沉淀的群聊事实与成员画像
└── `[群名]_log/`    # 若需要，可存放归档的长文本

## 核心逻辑
1. 每天定时任务（如 03:05，紧接随笔之后）触发 `group_memory_daily.json`
2. 读取昨天积累的 `wechat_group_monitor/events.jsonl`（或其它原始群聊记录源）
3. 提取其中的【成员事实】和【关键事件】，写入对应群名文件
4. 清除或截断已读取的原始 log（保留最新少量以免断层）
5. 生成/更新面向 agent 的【时间线型群聊记忆/成员事实入口】；对 `xinghui:main` / clawbot 场景，必须按 `wechat_context.group_timeline` 或运行时旁侧上下文注入，禁止恢复 `group_summary`/“群摘要”式 session 记录，禁止把同期完整群聊原文写入 `backend_history`。
