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

## 来源暗号提取（2026-10-19 已验证）
- **来源标注由服务侧写、GA 只提取，严禁 GA 端硬编码来源**。微信私聊来源行由 Win176 私聊脚本生成，含暗号 `【.跨次元微信私聊.】`；GA `ga_api_server.py` 用正则从 user content 提取 `【.xxx.】` 作为落库 source。
- 历史教训：GA 曾硬编码 `src = "【.微信私聊.】"`，等于 GA 伪造来源。正确做法是服务侧写暗号、GA 提取；无暗号时才回退默认来源。
- 只有微信私聊互动带 `【.跨次元微信私聊.】`；群聊/其他渠道不得复用此暗号。
- 验证：发一条微信私聊 → 查 `temp/api_chat_history.json` 最新 user 记录 `source` 是否等于 `【.跨次元微信私聊.】`。

## 上下文负担：脚本不得重复注入历史（2026-10-19 已验证）
- GA 自封装 API 已按 `chat_history.max_turns`（默认 30 轮）自行维护历史，**前端脚本只发本轮新消息**，严禁把「较早历史 / 历史背景」整段拼进 prompt。
- 历史教训：Win176 脚本曾把「30 条历史 + 新消息」整份当 user content 发给 GA，GA 原样落库 → 每条记录 99% 是重复旧历史（实测 8430/8495 字符），上下文平方级膨胀。
- 静默轮次（空回复 / `{"continue": false}`）GA 不落库，属设计行为。
