# local_frontends 插件层

GA 的本地前端增强插件层（独立 git 管理，避免 GA 主仓升级覆盖个性化改动）。当前目标是保持官方原生 GA/记忆体系，只在 clawbot 与微信群聊相关能力不足处做外挂增强。

## 当前保留

- `wechat_media_batch.py` — clawbot 入口增强 wrapper：媒体批处理、回复清洗、分气泡、运行时上下文等。
  - **图片表情包方案**：clawbot 图片表情包只保留“按分类在分类内随机发”的主路，输出独立气泡 `[[EMOJI:分类]]`，由 `emoji_library.py` 从 `data/agents/xinghui/emojis/<分类>/` 随机选图并走 `send_image`。不要把“按具体图名/文件名记忆并生成”作为方案；`[xxx.gif]` / `[xxx.jpg]` / `[xxx.png]` 已废弃为普通文本，不触发本地图片发送。微信系统 emoji 可继续用 `[表情名]` 文本混排，和图片表情包分开；`【表情名】` 也只作为普通文本/语义提示，不触发本地图包发送。
  - **泄漏清理方案（勿改 wechatapp.py）**：清理只在 clawbot wrapper `send_text_patched` 出口做，不改 GA 原生 `frontends/wechatapp.py`。`_LEAK_BLOCK_RE` / `_LEAK_LINE_RE` 负责删除 `<summary>...</summary>`、`[THOUGHT]`、`[WORKING MEMORY]`、系统注入块等内部标记；`_strip_task_done_trailing_note()` 负责处理模型已把 summary 标签剥掉、只在 `[任务已完成]` 前留下最后一段裸内部说明的情况；`_send_one_payload()` 在分气泡插件之后对每个气泡再次调用 `_visible_dialog_only()`，防止 summary 被拆成独立气泡后绕过清洗。清洗原则：优先按成对标签/系统标记/任务完成尾段结构删除内部内容，避免按“回应/表达/询问”等语义词硬编码。
  - **L4 归档方案**：`_append_l4_wechat()` 在 clawbot 收到用户文本、发出Agent可见回复时同步追加到 `memory/agents/xinghui/L4_sessions/wechat/mainline/{archive_day}.md`；按 agent_dream_review 的 08:00 切日规则归档，只记录可见对白，不记录工具/stdout/summary。
- `wx_plus_runtime_context.py` / `wx_plus_proactive.py` — clawbot 侧必要运行时上下文和主动性辅助。`wx_plus_proactive.py` 只能把预设句当作内部触发线索，必须通过同一个 `wx.agent` / xinghui 微信 session 生成最终可见回复，再交给 patched `send_text` 出口发送；禁止恢复为绕过模型与会话历史的固定文案直发。
  - **群聊感知边界（2026-06-05 确认）**：
    1. 已总结群聊 = Agent的时间线记忆，保留在 `main:session` 的 `group_timeline`，不可丢弃。
    2. 用户在 clawbot 私聊提起群聊时 → `_build_mentioned_group_live_context()` 临时注入同期未总结完整群聊原文（群友+Agent全部对话），只当轮可见，不写进 session history。
    3. Agent主动发起 clawbot 时 → `_recent_group_hint()` 读取同期未总结群聊完整原文作为旁侧触发背景（私有 trigger prompt，不进 session history）；**有内容则注入，没有则自然看不到；禁止用概率系统控制**；他是否以此为话题由他自己决定，系统不干预。
- `export_group_runtime_context.py` — 群聊侧只读 JSON 上下文导出；必须保持 stdout 纯 JSON，禁止导入会打印日志的前端入口。
- `qap_custom.py` — Q 前端备用文件，未纳入当前清理目标。

## 已退役/不得恢复为运行入口

- `wechatapp_custom.py` / `wechatapp_official_plus.py`：旧 clawbot 自定义前端/代理层。当前不再作为运行架构；需要参考旧逻辑时只看归档备份，不重新接入。
- 旧多 agent/API/agent_manager 兼容层：不再作为 xinghui-ga 的目标架构。

## OpenAI 兼容 API 配置说明

API 入口文件为 `frontends/openai_api.py`。它只是一个 OpenAI-compatible 前端，真正产生内容的仍然是 GA 内部 session；第三方平台里填写的 `model` 是“对外伪模型名”，由配置映射到实际 session。

### 配置文件位置

优先读取：

1. `plugins/local_frontends/config/api_config.json` — 本机私有配置，实际运行优先使用；
2. `plugins/local_frontends/config/api_config.example.json` — 示例/分发默认值，仅在私有配置不存在时兜底。

首次部署时：

```bash
cp plugins/local_frontends/config/api_config.example.json \
   plugins/local_frontends/config/api_config.json
```

然后只改 `api_config.json`。不要把私有 `api_config.json`、真实 token 或个人模型名提交进分发仓。

### 字段含义

```json
{
  "auth_token": "sk-your-token-here",
  "default_port": 8765,
  "model_session_map": {
    "default-chat": "main:session",
    "helper-chat": "helper:session"
  },
  "default_model": "default-chat",
  "model_owner": "your-app-name",
  "service_name": "your-app-name",
  "user_agent": "your-app-name-openai-api/1.0",
  "role_names": {
    "system": "第三方平台系统提示",
    "developer": "第三方平台开发者提示",
    "user": "用户",
    "assistant": "助手"
  },
  "api_status_text": "刚通过 OpenAI兼容API / 外部客户端聊天。"
}
```

- `auth_token`：第三方平台请求 API 时使用的 Bearer Token。请求头示例：`Authorization: Bearer sk-your-token-here`。
- `default_port`：API 默认监听端口；当前常用为 `8765`。
- `model_session_map`：最关键配置。
  - 左边 key：第三方平台可见、可填写的伪模型名，例如 `default-chat`。
  - 右边 value：GA 内部实际 session，例如 `main:session`。
  - 因此模型名可以自定义，真正决定共槽的是右侧 session。
- `default_model`：请求没传 `model` 时使用的默认伪模型名；必须是 `model_session_map` 里存在的 key。若填错，程序会自动退回第一个可用模型。
- `model_owner`：`GET /v1/models` 返回的 `owned_by` 字段。
- `service_name`：日志和服务名展示用。
- `user_agent`：API 内部下载远程图片等请求时使用的 User-Agent。
- `role_names`：把 OpenAI messages 里的角色转换为可见历史文本时使用的显示名。第三方平台的 system/developer prompt 会被当作“控制/背景提示”处理，不应直接污染为角色对白。
- `api_status_text`：写入运行状态/前端状态感知时使用的简短说明。

### 自定义伪模型名示例

如果第三方平台里想填 `my-app-chat`，但仍然接入 `main:session` 共槽：

```json
{
  "model_session_map": {
    "my-app-chat": "main:session",
    "helper-chat": "helper:session"
  },
  "default_model": "my-app-chat"
}
```

第三方平台请求：

```json
{
  "model": "my-app-chat",
  "messages": [
    {"role": "user", "content": "你好"}
  ]
}
```

实际写入/读取的仍然是 `main:session` session；其他前端若也接 `main:session`，两边就是同一个内容槽。

### 启动与覆盖优先级

配置文件之外，API 还支持环境变量和启动参数临时覆盖：

```bash
GA_OPENAI_API_TOKEN='sk-xxxx' GA_OPENAI_API_PORT=8765 \
python3 frontends/openai_api.py
```

或：

```bash
python3 frontends/openai_api.py --port 8765 --token 'sk-xxxx'
```

优先级：

1. 命令行参数：`--token`、`--port`；
2. 环境变量：`GA_OPENAI_API_TOKEN`、`GA_OPENAI_API_PORT`；
3. 配置文件：`api_config.json` / `api_config.example.json`。

### 第三方平台填写方式

常见 OpenAI-compatible 平台中：

- Base URL：`http://<服务器地址>:8765/v1`
- API Key：填写 `auth_token`，例如 `sk-your-token-here`
- Model：填写 `model_session_map` 左侧的伪模型名，例如 `default-chat` 或你自定义的 `my-app-chat`

可用模型可用下面命令核验：

```bash
curl -s http://127.0.0.1:8765/v1/models \
  -H 'Authorization: Bearer sk-your-token-here'
```

### 修改配置后的生效方式

`api_config.json` 是进程启动时读取的，改完需要重启 API 服务或重新启动脚本。

systemd 用户服务示例：

```bash
systemctl --user restart xinghui-ga-api.service
systemctl --user status xinghui-ga-api.service --no-pager
```

若是手动运行，则停止旧进程后重新执行：

```bash
python3 frontends/openai_api.py
```

### 注意事项

- 伪模型名只是第三方平台调用用的 ID，不等于底层真实模型。
- 要让 API 与 clawbot/其他前端共用历史，必须把伪模型名映射到同一个 session，例如都落到 `main:session`。
- 不要把第三方平台的大段 system prompt 当成主线对白写入；API 会尽量只保留可见对话内容。
- 不要在分发仓提交真实 token、私有配置、个人会话名；分发仓只保留 `*.example.json`。

