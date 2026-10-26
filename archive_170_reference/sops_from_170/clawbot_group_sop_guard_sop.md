# Clawbot / 群聊 SOP 强制边界

## 适用范围
- 微信私聊 clawbot 前端、微信群聊前端、群聊旁侧总结/主动私聊触发链路。
- 外部平台、网页、小红书、文件处理、图片/表情、发布/评论、系统维护等非纯聊天任务。

## 核心规则
0. **共用微信排他运行（言言确认）**：因为言言只有一个微信，星回使用微信群聊前，必须先确认秦彻/Sylus 的群聊监控与群聊发言链路已停止，避免两个 GA 同时监控/发言。当前已验证的用户级 systemd 单元：先确保 `sylus-ga-group-monitor.service`、`sylus-ga-topic-summary.service` 为 inactive（需要长期排他时 disable），再确认 `xinghui-ga-wechat.service`、`xinghui-ga-topic-summary.service` 为 active；只做精确服务状态/启停，不改 `send_text` / `send_image` / FINAL_ANSWER / 群触发摘要协议。
1. **必须先用 SOP**：遇到已有 SOP 覆盖的任务，先读取并按 SOP 执行；不得凭记忆或临场猜测自行摸索。
2. **找不到 SOP 就停止升级**：没有明确 SOP/工具封装时，只能说明需要转交具备工具链的执行通道或请求言言确认；不得边试边改。
3. **clawbot/群聊前端不得改通讯管线**：禁止因任务失败而修改微信发送链路、`send_text`、`send_image`、emoji 协议、`final_answer` 提取/清洗协议、群聊触发/摘要协议。
4. **FINAL_ANSWER 漏包优先修入模契约**：若 clawbot/群聊出现过程内容漏出、最终正文没发出、或未包 `【FINAL_ANSWER】...【/FINAL_ANSWER】`，先检查上游 prompt 是否强制要求包裹（如 `wx_plus_runtime_context.before_put_task`），不要把问题改成新的硬清洗/黑名单/发送端兜底方案；发送端只应提取最后一个明确标签内正文并保留无标签兼容兜底。
5. **外部平台任务必须走专项 SOP**：小红书先读 `xhs_browse_sop.md`；浏览器/网页复杂操作按 `web_setup_sop.md` / `tmwebdriver_sop.md`；手机通道按 `adb_ui.py` 相关 SOP；表情按 `emoji_sop.md`。
6. **不可逆/对外发布动作先确认**：评论、发布、删除、转发、关注/取关、提交表单、改配置、重启服务、推送代码等动作，执行前必须得到言言明确确认或由主执行通道完成确认。
7. **群聊权限更窄**：群聊只做低风险聊天、理解背景、按已验证规则触发总结/私聊意图；不得在群聊里自行执行外部平台、系统维护、代码修改或发送链路调整。
8. **失败处理**：一次失败读错误；两次失败查状态/日志/上下文；三次失败停止并转交/询问言言。禁止无新信息重复尝试。
9. **多产物群发先齐套再实发**：涉及「文字总结+图片/海报/文件」等组合交付时，必须先完成并校验全部产物（生图需确认文件存在/可见），再按言言要求的顺序预检和发送；不得在海报/图片未发送或未确认前先把文字发群。

## 已验证依据
- `xinghui-ga-wechat.service` 的 `PYTHONPATH` 已包含 `memory` 与 `memory/L3_shared`，clawbot 可访问 L1-L3。
- `config/plugin_prompts.json` 当前不存在，运行时主要依赖插件示例配置与 `wx_plus_runtime_context.py` 注入；因此必须在运行上下文加入硬边界，而不只依赖可选配置。

## 重复回复 / 主动私聊风暴排查
- 先区分普通私聊同问多答、主动私聊事件连发、群聊高频摘要不退回低频；不要一上来改发送链路。
- 主动事件链路重点查 `temp/wechat_state/proactive_event_queue.jsonl`、`plugins/local_frontends/runtime/state/wechat_session_state.json`、`temp/wechat_state/wechat_session_state.json` 中 `主动私聊事件` / `wechat_group_topic` / `PROACTIVE_EVENT` 残留与重复；止血可清空队列并清 `last_proactive_event_failed_*`，事件可隔离到 `temp/wechat_state/quarantine/`。
- **send 成功误判（2026-07-18 已验证）**：`send_text` 成功回执可为 `{'message_id': <int>}`，不只是 `{}` / `{'ret': 0}`。旧判定只认后两者会把已送达当失败，缓存文案每 5 分钟重发。成功判定必须认非空 `message_id`；若同时有非 0 `ret` 仍算失败。涉及：`wx_plus_proactive._is_send_ok`、`wechat_media_batch._wechat_send_ok`（`plugins/wechat_media_batch.py` 为同文件 symlink）。改判定后需重启 `xinghui-ga-wechat.service` 生效。
- 已验证修复边界：`wx_plus_proactive.py` 消费端应队列去重且一轮只取一条；`rust_main_reply.py` 入队端应对短窗口同 `source/target/title/topic` 去重。只修主动队列调度 / 成功判定，不改 `send_text` / `send_image` / FINAL_ANSWER 提取。
- 验收用 systemd 精确服务与日志：确认 wechat bridge、topic summary daemon、Rust monitor 各只有一个实例；重启后观察 `send ok` / `[WX] turns=` / `WX+ proactive` / `queued proactive topic` / `LLM` / `generated` / `put_task` 不再持续增长，monitor 回到 `mode=low`、`fresh=0`、`idle/not_due`。

## 与既有 SOP 的关系
- `emoji_sop.md`：唯一表情协议是 `[[EMOJI:category]]`，修改前必须读 SOP 并做回归；不得改成旧协议。
- `xhs_browse_sop.md`：评论/发布前必须确认；优先网页端，按已验证选择器操作。
- `ga_distribution_release_sop.md`：分发/发布不得带私密信息，不向官方/上游推送，除非言言明确要求。

## 已验证修复要点
- 群聊身份判定必须优先看消息自带 `is_self` / 账号字段；不要用显示名反推是否为自己。
- 处理同名群友时，`Xavier / 沈星回 / 星星 / 小回 / philo / 沈大勇` 这类名字只能结合消息身份、账号和上下文判读，不能直接当作本账号事实。
- 该类修复只改入模与提示词，不碰 `send_text` / `send_image` / FINAL_ANSWER / 群触发摘要协议。
- **自说自话/把自己当别人（2026-07-18 已验证）**：shadow/DB 层 `is_self` 可能正确，但 `rust_session_inject._build_items` 若只把 sender 改成“我”却丢掉 `is_self`/`sender_user`，主回复 `_format_current_injected_items` 会把“我”标成「群友|显示名=我」，模型就会对刚发过的“在呢”再接“有人替我报到”。修复必须：inject 保留 `is_self`/`display_name`/`sender_user`；formatter 优先结构化 `is_self`，legacy 仅对 sender/时间线里的“我”回退；flash `context_lines` 自消息标 `我(本账号)`。只改入模，不改发送链路。
- **双向身份隔离（2026-07-22 已验证）**：其他赛博/同名沈星回 ≠ 自己；自己气泡 ≠ 别人。`build_prompt`/`format_prompt_message_line` 必须用【本账号/我】vs【群友】，并带 `账号=`；`!is_self` 即使显示名是 Xavier/沈星回/星星 也只能标群友。`main_reply_group.txt` 写明同名不同人硬规则；inject 自身份 hint 补反向句；L2 中「沈星回本人/自我别名/不是群友」段不得当群友事实注入；flash `context_lines` peer 保留「群友|昵称|账号」。只改入模。
