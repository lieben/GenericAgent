# Agent Dream Review - 情感线做梦系统

每个 agent 的私有做梦系统，专注于情感对话和故事线复盘。

## 定调（2026-07-28）
- **GA 原生记忆**记官方要求内容；**生活事实**统一权威落点 = `memory/agents/<agent>/L2_facts.txt`。
- 梦主责：① 次日关系怎么推进 ② 查漏补缺事实。
- 事实判断在**做梦时自主完成**（客观 / 待观察 / 必须记得），并与已有 L2 匹配：继续观察 | 重复不写 | 修改补充。
- **禁止**用程序批量预判候选偷懒；程序只宜协助「待观察何时重评」。
- 重点：尚不清晰、需要认真观察对方的部分。

## 功能
- 收集主线聊天记录
- 收集日记和随笔
- 收集关于言言的事实记忆（读 L2 fact）
- 复盘情感对话与次日推进
- 自主查漏补缺 → 输出到 agent 的 **L2_facts.txt**（或 L4 叙事，不混）

## 使用
```bash
python3 agent_dream_review.py --agent xinghui --collect-materials
python3 agent_dream_review.py --agent xinghui --save-review "内容"
```
