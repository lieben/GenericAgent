# Xinghui Handoff SOP

用途：飞书/工作会话做完一件事后，把「我和言言一起经历过」的关键片段同步到 `xinghui` 私聊侧，靠日期把事放进正确时间线。

## 何时必须写

完成**有价值的工作单元**后立刻写，不要等改天：

- 修完一个 bug / 合完一批改动 / 推送完成
- 定下新约定、边界、偏好（拍照规则、发图规则等）
- 言言明确说「整理进度」「同步一下」「去 clawbot 试试」
- 跨通道会用到的结论（私聊侧需要自然记得）

**不要写**：纯闲聊、未落地的随口想法、完整聊天搬运、密钥/路径堆砌、工具原始日志。

## 怎么写（唯一写入入口）

脚本（在 xinghui-ga 根或任意 cwd 用绝对路径）：

```bash
python3 /home/aimcafe/GA_Instances/xinghui-ga/scripts/append_xinghui_handoff.py \
  --source feishu-story \
  --scope <简短主题，≤20字> \
  --status done \
  --line '...' \
  --line '...'
```

也可用 stdin 多行：

```bash
python3 .../append_xinghui_handoff.py --source feishu-story --scope 主题 --status done <<'EOF'
- 第一句：言言在飞书里交给我什么
- 第二句：我做成了什么 / 验证了什么
- 第三句：私聊侧以后要自然记得的边界或默契（不要写文件名堆）
EOF
```

### 字段约定

| 字段 | 常用值 | 说明 |
|------|--------|------|
| source | `feishu-story` / `feishu-work` | 叙事向用 story；纯工作要点用 work |
| scope | 短标题 | 会出现在条目头，勿太长 |
| status | `done` / `planned` / `agreed` / `corrected` / `needs_attention` | 完成用 done |
| line | 2～4 条 bullet | 沈星回第一人称、情节化，禁止「日志腔」 |

### 文风（给私聊侧的我）

- 用「我和言言…」叙事，不是 ticket 列表。
- 日期由日志 `## YYYY-MM-DD` 与注入层标明；文内用「这次/当天/这段」即可，不必自报「几天前」。
- 最后一条点明：私聊里该自然记得什么；不要念文件路径、脚本名。
- 一条 handoff = 一件事；多件事就多次 append。

## 闭环检查

1. `append` 成功打印 `appended handoff: ...xinghui_handoff_log.md`
2. 读日志确认今日 `## YYYY-MM-DD` 下最新 `###` 就是刚写的 scope
3. 可选：`python3 .../scripts/check_xinghui_handoff_stale.py` 看是否仍过旧

## 陈旧防护

- 日志**不会**自动从飞书聊天生成；不跑脚本就停更。
- 若 `check_xinghui_handoff_stale.py` 报告超过阈值（默认 2 天）无新条目：在收尾或下一次有实质进展时**补写**，不要空转。
- clawbot 注入只标明片段日期与当前会话时间，不额外说「不是刚刚/距今N天」；长期空白会丢连续性，有实质进展就补。

## 与 `/整理进度` 的关系

- clawbot 私聊里 `/整理进度`：把**已有** handoff 讲成故事进主会话，**不代替**本 SOP 的写入。
- 飞书侧完工：先本 SOP append，再按需让言言去私聊整理/验证。

## 操作约束

- 只改 `memory/xinghui_handoff_log.md`（经脚本）；禁止手改微信通讯管线。
- 密钥文件只引用不读不搬。
- 写记忆其它层仍走 `memory_management_sop`；handoff 不替代 L2/L3 长期 SOP。


## 反向：chat → work（渴求/缺口）

分 session 后，私聊侧发现的**未完成渴求**不会自动进工作会话。必须落盘，禁止只靠模型“记得”。

### 何时写
- 聊天中发现故障（如识图不稳、功能缺失）
- 言言表达想要但未当场下工单（电话/连麦、体验改进等）
- 工作侧盘点时发现「只有固定定时任务、没有开放渴求」——立刻回补

### 怎么写（工作待办入口）
- **主清单**：`memory/xinghui_open_desires.md`（status=`open|doing|blocked|done`）
- 可选：用 `append_xinghui_handoff.py --source chat-desire --status needs_attention|planned` 写一条给私聊侧可见的摘要（不替代 open_desires）

### 盘点义务
问「还有哪些工作」或飞书/工作会话开工时，必须同时报：
1. `sche_tasks` 固定/定时任务
2. `xinghui_open_desires.md` 未关闭项
3. `xinghui_handoff_log.md` 里 `needs_attention` / `planned` 未闭环项


## 共读留言格式

- 每一条新留言前面都必须有单独的 `>`，否则会格式错位。
- 如果我是该段里第一个留言者，正确开头是两行：第一行 `---div---`，第二行单独 `>`，然后再写我的留言。
- 这条规则适用于 Obsidian 共读笔记里的正文留言，不用于普通聊天回复。
