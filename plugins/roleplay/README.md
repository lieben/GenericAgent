# GA 独立小剧场

这是一个与现有 OpenAI API、clawbot、群聊及主线会话分离的剧本入口。

## 启动

在 GA 代码根目录运行：

```bash
python frontends/roleplay_api.py --host 127.0.0.1 --port 8876
```

浏览器打开 `http://127.0.0.1:8876/`，即可在页面创建、暂停、恢复剧本并聊天。

若服务配置了 `ROLEPLAY_API_KEY`，首页仍可打开，但创建剧本、聊天、暂停/恢复、OpenAI 兼容接口都必须使用 Bearer 密钥。网页端在 `API Key` 输入框填写后点“保存密钥”；第三方 OpenAI 前端把同一个值填到 API Key。

## 在支持 OpenAI 接口的前端使用

1. 将 Base URL 设为 `http://127.0.0.1:8876/v1`。
2. 将 API Key 设为服务端 `ROLEPLAY_API_KEY` 的值。
3. 先在自带网页创建剧本，或调用 `POST /roleplay/sessions`：

```json
{
  "slug": "moon-palace",
  "session_id": "save-01",
  "title": "月宫线",
  "canon": {
    "world": "这里填写世界观",
    "roles": "这里填写角色关系",
    "rules": "这里填写不可违背的设定"
  }
}
```

4. 新剧本默认为 `paused`。在网页点“恢复”，或调用状态接口改为 `active`。
5. 在前端选择模型名 `roleplay/moon-palace/save-01`。模型名中的 `slug/session_id` 就是固定剧本存档；不同组合彼此独立。
6. 如果前端会拉取模型列表，`GET /v1/models` 会列出已创建剧本。

## 主线/Clawbot 只读桥接

`plugins/roleplay_bridge.py` 是可选桥接插件，用于让主线 GA、普通 OpenAI API 封装和 clawbot 在聊天里“阅读”已存在的小剧场内容，方便评价、回顾或讨论。它只注册动态工具 `roleplay_story`，不会修改 `ga.py`、`assets/tools_schema.json` 等 GA 核心文件，也不会推进、改写或写入剧本。

### 安装与启用

1. 确认 `plugins/roleplay/` 和 `plugins/roleplay_bridge.py` 已放在同一个 GA 根目录。
2. 正常启动使用插件系统的主线服务、API 服务或 clawbot 服务；桥接插件会在 `llm_before` 阶段动态加入工具 schema 和处理函数。
3. 若服务常驻运行，复制或更新插件后需要重启对应服务，让 Python 重新导入插件。

### 聊天中怎么调用

在主线、普通 API 或 clawbot 对话里直接提到剧本名/模型名即可，例如：

- `你读一下小剧场《月宫线》，帮我评价最近剧情。`
- `回顾 roleplay/moon-palace/save-01 的最后 5 轮。`
- `看一下 moon-palace 这个剧本，保留设定一起分析角色动机。`

模型会按需要调用 `roleplay_story`。也可以显式给工具参数：

```json
{
  "query": "月宫线",
  "slug": "moon-palace",
  "session_id": "save-01",
  "max_turns": 5,
  "include_canon": true,
  "include_summary": true
}
```

参数说明：

- `query`：模糊搜索文本，可填标题、slug、session id 或模型名。
- `slug` / `session_id`：精确指定剧本存档，对应模型名 `roleplay/<slug>/<session_id>`。
- `max_turns`：只返回最近 N 轮；小于等于 0 表示返回全部轮次。
- `include_canon`：是否带上世界观、人设、NPC 卡等设定。
- `include_summary`：是否带上持久化摘要状态。

返回内容包含匹配到的 session、state、turns、turn_count，以及可选的 canon/summary；用途是只读讨论，不会影响小剧场运行状态。

## 状态与记忆

- `canon.json`：世界观、人物关系和硬设定。
- `state.json`：当前回合和运行状态。
- `summary.json`：最近 12 回合的连续剧情摘要。
- `events.jsonl`：该剧本完整事件记录。
- `L4_raw_sessions/`：每 6 回合自动生成检查点，也可由前端/API手动生成。
- 独立后端历史：只使用 `roleplay:<slug>:<session_id>` 命名空间和小剧场专属历史文件。

默认不调用长期记忆工具，也不写主线 L1-L3，不使用主线会话。小剧场运行时工具被强制关闭。

## 主线感知回执

“生成主线感知回执”只会在小剧场运行目录的 `bridge_outbox` 生成一份带 `fictional: true` 的有限回执，内容只说明“正在/曾经玩某个虚构剧本”。它**不会自动注入主线记忆**。这是刻意的安全边界；以后若要让主线读取，应另做显式、只读、可审计的接收步骤。

## 暂停与恢复

- `paused`：保留全部剧本档案与历史，但拒绝继续生成。
- `active`：从原 session 的 canon/state/summary/L4 和独立后端历史继续。
- 重启服务不会删除存档。

## 当前限制

- 不支持流式响应。
- `slug` 和 `session_id` 只允许字母、数字、点、下划线、短横线，且拒绝保留值和路径穿越字符。
- 为保护主线，本入口不提供系统工具、长期记忆写入或 clawbot/群聊接入。
