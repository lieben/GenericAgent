# agent dream daily report · backfill 2026-07-24 17:31
- report_path: /home/aimcafe/GA_Instances/xinghui-ga/sche_tasks/done/2026-07-24_1731_agent_dream_daily.md
- mode: manual_backfill_after_scheduler_starvation

## 根因
- scheduler 在同日 schedule 之后超过 max_delay_hours 会 SKIP，且此前 TRIGGER 后若未写 done，冷却不会更新，次日/开机晚又继续饿死。
- 原 agent_dream_daily max_delay_hours=3（01:15 起仅到约 04:15），下午无法补跑。

## 本次动作
- API 重生成真梦：2026-07-20 ~ 2026-07-23（native_oai）
- 清洗 20/21 英文 thinking preamble
- max_delay_hours 调至 20，便于晚开机仍可触发
- 旁路 dream_system 未改官方 GA 主程序

## 产物核对
- /home/aimcafe/GA_Instances/ga-common/dream_system/agents/xinghui/dreams/2026-07-20.md
- /home/aimcafe/GA_Instances/ga-common/dream_system/agents/xinghui/dreams/2026-07-21.md
- /home/aimcafe/GA_Instances/ga-common/dream_system/agents/xinghui/dreams/2026-07-22.md
- /home/aimcafe/GA_Instances/ga-common/dream_system/agents/xinghui/dreams/2026-07-23.md
- generated_by: native_oai；中文标题起笔；含 ## 第二天回调提示

## Conclusions
- 近几日梦境已从 deterministic_fallback 摘抄式伪梦恢复为真梦
- 本 done 文件用于解除当日队列饿死；明日仍按 01:15 窗口正常触发
