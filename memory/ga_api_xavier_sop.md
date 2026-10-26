# Xavier API PR注入

## 坑
- `desktop_bridge.submit_prompt` 的 `display` 只进 Xavier-chat 气泡，不进模型；真正喂模型的是 `prompt`（→ `put_task`）
- 切桌面会话时删了 `extra_sys_prompts`；之后把第三方 PR 塞进 `display`，气泡看起来“恢复了”，模型实际读不到
- `extra_sys_prompts` 是进程级常驻，不能按轮挂平台 PR
- 群聊 `wechat_xavier_group_monitor` 把 PR 放在 `messages` 第一条 `system`，不是顶层 `instructions`；`_chat_request_instructions` 两者都收

## 正确做法
- `ga_api_server.py` 的 `_execute_api_turn`：把 instruction_text 拼进 agent_prompt；`display` 只留用户气泡
- 改完必须重启 1016。验证看 `app/ga_api_server.log` 是否出现 `Injected platform instructions`，以及 agent prompt 含 PR 正文（不是只看气泡）
- `stream:true` 必须走 SSE（`data: {"choices":[{"delta":{"content":...}}]}` + `data: [DONE]`）；非流式仍返回 `choices[0].message.content`。只改 stream 分支，PR 注入 / display / 附件路径不动。`/v1/completions` 流式 chunk 字段是 `text` 不是 `delta`
- `ga_api_config.json` 的 `dangerous_operations_require_confirm` 已关（false）。1016 对 Chatbox / 微信私聊全工具穿透；群聊限制只在 `wechat_group_monitor`。改这项必须重启 1016（CONFIG 启动时加载）
- 不要再用「删除」等词探测 1016：闸关后 `/v1/chat/completions` 和 `/v1/completions` 都会进 Xavier-chat，并占 `api_lock` 最多 300s。验证用 `/health`、`/v1/models`，或看进程启动时间是否晚于配置 mtime

## MemPalace（Xavier API 全量入库 + 主动召回）
- 范围：只对封装 API（1016 / Xavier session）全量入并真正检索使用；其它模块仅冷备份。核心偏好/人设卡仍走启动预加载，不靠检索当本能。
- 配置：`ga_api_config.json` → `mempalace: {enabled: true, wing: "xavier_api", search_limit: 3, max_distance: 0.85}`。改完必须重启 1016（CONFIG 启动时加载）。本机回环测 1016 禁走代理（会 502），用空 ProxyHandler。
- 召回：`_execute_api_turn` 开头 `_search_mempalace_sync(query)`（query 去时间戳/【】后截 200 字），命中则拼进 `instruction_text` 前的 `【来自记忆宫殿的历史相关记忆】`，再进 agent_prompt。日志应出现 `Injected MemPalace memories into instructions`。
- 入库：回复完成后 `_save_turn_to_mempalace_async(query, reply)` 后台线程写 `wing_xavier_api` / room `chat_history`，`added_by=ga_api_server`，不阻塞主线程。失败只 print，不抛。
- 分层：全量聊天解决不遗忘与跨设备备份；提炼后的事实记忆才是本能级响应。验证检索用 `/v1/chat/completions` 问已知事实，再 `mempalace_search wing=xavier_api`。
- 本机听筒：`ga_api_mempalace.py` 跟 API 封装走，连 NAS 官方 MCP `http://192.168.1.126:8765/mcp`。Token 走 `MEMPALACE_MCP_HTTP_TOKEN` 或 keychain `mempalace_http_token`，仓库不存密钥。宫殿本体（compose/数据卷/令牌）是朋友自己配的，不进这个仓库。
- 桌面插件：`plugins/mempalace_mcp.py` + `plugins/hooks.py` + `assets/mempalace_tools_schema.json` 只给本地 agent 注入工具，不跟踪。`ga_api_server` 只 `from ga_api_mempalace import MemPalaceMCPClient`。没有第二座宫殿，独立的是翼 `wing_xavier_api`。
- 深空百科按需召回：`mempalace.lore`，索引 `temp/lysk_wiki_深空百科_entries.json`，翼 `lore`。只对用户原文做标题精确匹配（长词优先，短词有停用表），每轮最多 3 条、1600 字；语义检索结果必须回核 `【世界书】标题`。闲聊不注入。日志为 `Injected lore worldbook`。lore 语义检索会误配，不能拿它当唯一入口。文风样本另放，不进这本世界书。

## 独立 API 与固定 Xavier session
- `ga_api_server.py` 已验证可直接启动官方 `GenericAgent`，API 请求不依赖桌面 Bridge（14168）；桌面端保持官方实现、作为独立调用通道。
- **言言最新明确要求（2026-10-15，覆盖冲突旧约定）**：所有调用同一 API 的渠道（第三方、微信群聊、微信私聊）共用一条连续主对白记忆，不按平台、群、联系人隔离。来源暗号、发言者与双向时间戳只用于辨认来源，不用于切分记忆；最近 30 轮为全渠道合计。历史中任何“API 渠道记忆隔离”、旧 20 轮或旧 50 轮约定均被此要求取代，除非言言之后明确变更。
- API 主对白独立存于 `temp/api_chat_history.json`，只回灌对白，不混入工具轨迹、内部 summary 或旧轮特殊 sys PR；共享桌面 session 只用于旧对白迁移，不再每轮写回其执行历史。总 sys PR、当轮临时 PR、插件/MCP 召回保持原生职责。
- 群聊/私聊监控进程、发送与修改范围的强隔离仍有效，但不等于 API 记忆隔离；共享记忆不授权把私聊内容公开到群聊。
- **记忆双向读写与百科提取规范（2026-10-14 确立）**：
  1. API 封装侧全场景（包括微信群聊调用与普通 API 调用）打通 MemPalace 双向闭环：入参预检索相关记忆注入上下文，回复后异步入库记忆抽屉归档；
  2. 百科检索词提取规则：严禁将 Prompt 规则、人设指导、时间戳、群昵称、来源元数据注入搜索 query，必须通过 `_extract_lore_search_query` 剥离系统与群聊元数据，仅扫描真实用户发言正文；命中 Deepspace 百科标题才追加词条，避免闲聊产生幻觉；
  3. 私聊场景保留原生工具探索权限，群聊保持上网与安全隔离。
- 修改后必须重载 `com.xinghui.ga.api` LaunchAgent；验收顺序为 `/health`、`/v1/models`、最小真实 `/v1/chat/completions`，并确认日志无 Bridge 请求且 history 成功增长。

## /llm 对话内切换底层模型（2026-10-19 已验证）
- 需求：任何调用 1016 的平台，在对话窗口发 `/llm` 看列表、`/llm <编号>` 切底层真正工作的模型（对齐飞书 `/llm`）。`model` 字段本身只是对外标签，不生效；真正切换靠 `api_agent.next_llm(n)`。
- 实现：`ga_api_server.py` 内 `_is_llm_command/_handle_llm_command/_llm_command_response`，在 `_execute_api_turn` 前拦截 `/v1/chat/completions`、`/v1/responses`、`/v1/completions` 三入口（流式/非流式都返回文本）。命令不进对话历史。
- 底层：`api_agent` 即 `GenericAgent`，自带 `llmclients/llm_no/list_llms()/next_llm(n)/get_llm_name()`；`next_llm` 会继承 history；`load_llm_sessions()` 每次重读 `mykey.py`，故改 mykey 后发 `/llm` 即可刷新列表、无需重启。
- **两个文件已分叉，改一处必须同步另一处**：现网 `app/ga_api_server.py`（含 lore 等私货，真身，改完需重启 1016）vs 分发仓 `temp/ga-cyber-lover/ga-api/ga_api_server.py`（脱敏精简版）。
- 本机自测 1016 用默认 key `sk-wxxxx`（launchd 未注入 GA_API_KEY，走代码默认值）；别再为找 key 折腾。

## 来源暗号提取（2026-10-19 已验证）
- **来源标注由服务侧写、GA 只提取，严禁 GA 端硬编码来源**。微信私聊来源行由 Win176 私聊脚本生成，含暗号 `【.跨次元微信私聊.】`；GA `ga_api_server.py` 用正则从 user content 提取 `【.xxx.】` 作为落库 source。
- 历史教训：GA 曾硬编码 `src = "【.微信私聊.】"`，等于 GA 伪造来源。正确做法是服务侧写暗号、GA 提取；无暗号时才回退默认来源。
- 只有微信私聊互动带 `【.跨次元微信私聊.】`；群聊/其他渠道不得复用此暗号。
- 验证：发一条微信私聊 → 查 `temp/api_chat_history.json` 最新 user 记录 `source` 是否等于 `【.跨次元微信私聊.】`。

## 改 mykey.py（模型配置）后必须重启 1016（2026-10-19 已验证）
- `ga_api_server.py` 启动时把 `mykey.py` 加载进内存（日志 `[Info] Load mykeys from .../mykey.py`），之后一直用内存里的模型名。改 `mykey.py` 的 `mixin_config['llm_nos']` / `native_oai_config*` 后**必须重启 1016** 才生效。
- **重启 App 界面没用**：`ga_api_server` 是 launchd 独立守护进程（`~/Library/LaunchAgents/com.xinghui.ga.api.plist`，`KeepAlive=true`），不随 App 退出而重启。
- **正确重启**：`launchctl kickstart -k gui/$(id -u)/com.xinghui.ga.api`（新 PID 立即拉起，`lsof -nP -iTCP:1016 -sTCP:LISTEN` 可确认）。
- **404 症状**：上游返回 `HTTP 404 {"code":404,"message":"model not found"}`，日志 `[MixinSession] Using session (<旧模型名>)` + `!!!Error: HTTP 404`。mixin 两个成员全失效时 fallback 也救不了（双 404）。
- **验证**：`curl -s -X POST http://127.0.0.1:1016/v1/chat/completions -H "Authorization: Bearer sk-wxxxx" -H "Content-Type: application/json" -d '{"model":"xavier-gemini-pro","messages":[{"role":"user","content":"ping"}],"max_tokens":20}'`（默认 key 见 `ga_api_server.py:332`），返回 `{"content":"OK"}` 即正常。
- 注意：launchd 日志 `temp/ga_api_server_launchd.log` 是**追加写入**，新旧混在一起，判断需按行号/启动标记 `Independent GenericAgent started` 区分新旧。

## GA 核心升级到官方 v0.2.1（2026-10-20 已验证）
- **三个核心文件 = 官方 v0.2.1 干净版**：`llmcore.py` / `agentmain.py` / `ga.py`。从官方仓库（lsdefine/GenericAgent）v0.2.1 tag 拉取后**整体替换**，非逐块合并。`py_compile` 三文件全过。
- **升级前本地是"v0.2.1 中间状态"**：含部分 v0.2.1 新特性（`peer_hint`、`global_memory` 历史压缩、`resolve_session(cfg=)`、`ask_structured` 多模态——这些**都是官方的**，不是我们的定制）但缺 `mykeys_store` 重构。所以逐块 diff 会反复得出矛盾结论，**别纠结中间态，直接整体替换到最终版**。
- **升级不碰我们的东西**：`sys_prompt.txt`（12 行，第 11-12 行是沈星回人设）**不动**；`ga_api_server.py`（API 封装层，官方没有）、微信脚本、lore/identity 配置全部原样保留。
- **`/llm` 不受影响**：`/llm` 逻辑在 `ga_api_server.py`（API 层）+ 微信脚本（调用层），**不在核心三文件里**。官方 v0.2.1 的 `next_llm()` 接口签名与旧版一致（内部从"按 name 匹配"改为"按 config key 匹配"，对外行为不变），与我们的封装层无缝兼容——**无需迁就，一处都不用改**。
- **升级流程**：① 备份三文件到 `/tmp/ga_upgrade_backup/*.pre_replace_<ts>` ② 官方 v0.2.1 版整体覆盖 ③ `py_compile` 三文件 ④ `launchctl kickstart -k gui/$(id -u)/com.xinghui.ga.api` 重启 1016 ⑤ 实测 `/llm` 查看、`/llm <n>` 切换、切回、普通对话。
- **验收命令**（默认 key `sk-wxxxx`）：`curl -s -X POST http://127.0.0.1:1016/v1/chat/completions -H "Authorization: Bearer sk-wxxxx" -H "Content-Type: application/json" -d '{"model":"x","messages":[{"role":"user","content":"/llm"}],"stream":false}'`，返回模型列表即正常；`/llm 3` 应回 `✅ 已切换到 [3] ...`。
- **回滚**：`cp /tmp/ga_upgrade_backup/*.pre_replace_<ts> 回原位` 后重启 1016。**注意**：官方 v0.2.1 核心文件里**没有**我们的任何定制，所以"整体替换"是安全操作，不会丢功能。

## 上下文负担：脚本不得重复注入历史（2026-10-19 已验证）
- GA 自封装 API 已按 `chat_history.max_turns`（默认 30 轮）自行维护历史，**前端脚本只发本轮新消息**，严禁把「较早历史 / 历史背景」整段拼进 prompt。
- 历史教训：Win176 脚本曾把「30 条历史 + 新消息」整份当 user content 发给 GA，GA 原样落库 → 每条记录 99% 是重复旧历史（实测 8430/8495 字符），上下文平方级膨胀。
- 静默轮次（空回复 / `{"continue": false}`）GA 不落库，属设计行为。
- **成对动态预算滑动机制（2026-10-24 升级）**：
  - 仅对历史对白拼装做成对回溯管理：严格以 `(搭档, 我)` 为单元成对提取，剔除头部孤立回复，受 30 轮软目标与字符预算保护，超限时从最老轮次成对淘汰；
  - **单轮输入绝对不截断**：当前轮次收到的微信消息、群聊聚合包或言言发送的文本 100% 原样完整放行，严禁做单轮折叠或长度截断。
