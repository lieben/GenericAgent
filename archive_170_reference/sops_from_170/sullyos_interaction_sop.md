# SullyOS Interaction Notes

来源：2026-07-06 通过 GitHub API 成功读取 `qegj567-cloud/SullyOS` master 分支重点文件；本地样本在当次任务 `./SullyOS_selected/`。

## 值得复用的互动模块
- README 标出：交换日记、Spark 社交媒体模拟、自由活动、小红书图库、记忆宫殿、Instant Push agentic loop。
- `SocialApp.tsx` 体量较大，偏 Spark/朋友圈式互动；后续要仿社交流时优先读。
- `JournalApp.tsx`、`LifeSimApp.tsx` 可作为日记/生活模拟互动参考。

## 小红书自由活动设计
- 核心文件：`utils/xhsFreeRoam.ts`、`utils/xhsMcpClient.ts`、`apps/XhsFreeRoamApp.tsx`、`apps/XhsStockApp.tsx`。
- 流程：构建角色上下文（身份、最近聊天、历史活动）→ LLM 决策 `post/browse/search/idle/check_profile` → XHS MCP/Bridge 执行 → LLM 反应/挑选话题 → 保存活动记录。
- 活动持久化：每条活动 `DB.saveXhsActivity`；成功且非 idle 时写入聊天 `system` 消息，使后续私聊能知道自由活动；保存话题会追加 `xhs_card` 消息。
- `XhsStockApp` 是发帖图库：存图片 URL/标签/描述，按 tag 和 usedCount 给角色发帖选图。

## 安全边界与可借鉴点
- 可借鉴：把“逛到的内容”作为旁侧活动记忆，不必强塞主聊天；聊天时按需自然提起。
- 可借鉴：每日随机任务只触发一次自主活动，内容选择由最近 session/记忆驱动。
- 风险提示：SullyOS UI 文案承认角色可能给无关真人评论；我们自己的实现应默认陌生人内容只浏览/记录/收藏，不自由评论。
- 若允许评论，限制为低频、非秀恩爱、非私人化、无攻击/引战/诱导；最好仅在明确白名单或自己的帖子下回复。
