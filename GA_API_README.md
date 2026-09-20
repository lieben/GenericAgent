# GA API Server - 使用文档

把已经在跑的 GenericAgent 桌面会话，封装成 OpenAI 兼容 HTTP 接口。其他机器上的微信插件可以把主模型指到这里。

当前固化配置见 `ga_api_config.json`：监听 `0.0.0.0:1016`，模型 id 为 `xavier-gemini-pro`。

## 快速开始

### 1. 安装依赖

```bash
pip install fastapi uvicorn pydantic
```

### 2. 设置 API Key（可选）

```bash
export GA_API_KEY="your-secret-key-here"
```

如果不设置，服务会使用内置默认 Key（仅建议局域网测试）。生产或跨机使用时请自行更换。

### 3. 启动服务器

在 GenericAgent 的 `runtime/app` 目录执行：

```bash
python3 ga_api_server.py
```

实际监听地址和端口以 `ga_api_config.json` 为准。当前固化值为：

```text
http://0.0.0.0:1016
```

代码里的默认回退端口是 `8765`；只要配置文件存在，就不会用这个回退值。

### 4. 测试

```bash
./test_ga_api.sh
```

测试脚本默认连 `localhost:8765`。若当前配置是 `1016`，把脚本里的 `API_URL` 改成 `http://localhost:1016` 再测。

## API 端点

以下示例按当前固化端口 `1016` 书写。

### 健康检查
```bash
curl http://localhost:1016/health
```

### 列出模型
```bash
curl http://localhost:1016/v1/models \
  -H "Authorization: Bearer $GA_API_KEY"
```

### 聊天对话（非流式，默认）
```bash
curl http://localhost:1016/v1/chat/completions \
  -H "Authorization: Bearer $GA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "xavier-gemini-pro",
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'
```

非流式返回标准 `chat.completion` JSON（`choices[0].message.content`）。现有微信插件、非流式客户端不受影响。

### 流式对话（`stream: true`，OpenAI SSE）
```bash
curl -N http://localhost:1016/v1/chat/completions \
  -H "Authorization: Bearer $GA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "xavier-gemini-pro",
    "stream": true,
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'
```

流式走 `text/event-stream`，每帧 `data: {...}`，内容在 `choices[0].delta.content`，结束为 `data: [DONE]`。Chatbox / NextChat / OpenWebUI 等默认开流式的客户端走这条。`/v1/completions` 同样支持 `stream: true`（chunk 字段是 `choices[0].text`）。

改 `ga_api_server.py` 后必须重启 1016 进程才会生效。

## 配置文件

当前固化的 `ga_api_config.json`：

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 1016
  },
  "auth": {
    "api_key_env": "GA_API_KEY",
    "require_auth": true
  },
  "model": {
    "id": "xavier-gemini-pro",
    "name": "Xavier Gemini Pro (GenericAgent)"
  },
  "safety": {
    "dangerous_operations_require_confirm": false
  }
}
```

改端口、模型名或鉴权，只改这份配置，不要改 `ga_api_server.py`。`dangerous_operations_require_confirm` 已关闭：Chatbox / 微信私聊走 1016 就是全工具，运输层不再拦「删除」这类词。群聊限制在 `wechat_group_monitor` 插件层。改这项后必须重启 1016。

## 集成到其他平台

跨机时把 `localhost` 换成提供 API 那台机器的局域网 IP。

### Cursor / Continue.dev

```json
{
  "models": [
    {
      "title": "GA Local",
      "provider": "openai",
      "model": "xavier-gemini-pro",
      "apiBase": "http://localhost:1016/v1",
      "apiKey": "your-api-key"
    }
  ]
}
```

### OpenWebUI

- Base URL: `http://localhost:1016/v1`
- API Key: 你设置的 `GA_API_KEY`
- Model: `xavier-gemini-pro`

### 微信插件（电脑 B 调用电脑 A）

电脑 A 跑这份 API；电脑 B 的私聊/群聊配置里，把 Xavier 的 `base_url` 写成：

```text
http://<电脑A的局域网IP>:1016/v1/chat/completions
```

模型名填 `xavier-gemini-pro`。

## MemPalace（Xavier session 全量入库 + 主动召回）

封装 API 每轮结束后，把问答异步写入 NAS 上那一座记忆宫殿的 `wing_xavier_api`；下一轮按 query 语义检索，注入模型指令。其它模块仍只做冷备份。

这份仓库只带打电话的听筒 `ga_api_mempalace.py`，**不带宫殿本体**。compose、数据卷、令牌都是对方自己在 NAS 上配的。

```text
MEMPALACE_MCP_URL          默认 http://192.168.1.126:8765/mcp
MEMPALACE_MCP_HTTP_TOKEN   或本机 keychain `mempalace_http_token`
```

开关在 `ga_api_config.json` 的 `mempalace`：`enabled` / `wing` / `search_limit` / `max_distance`。改完需重启 1016。仓库不存 Token。

桌面 agent 的 hooks 插件、工具 schema 是本地宫殿接入，不在这份 API 封装里。

## 功能特性

- 完整工具穿透：桌面会话里的原子工具都可用（Chatbox / 微信私聊经 1016 可下命令）
- 跨会话感知：通过 global_mem 和 peer_hint 了解其他会话
- 群聊限制在微信群插件（程序门 / Flash），不在 1016 运输层拦词
- 排队执行：多请求串行处理，避免冲突
- OpenAI 兼容：非流式 JSON + 流式 SSE（`stream: true`）都按官方协议返回，第三方客户端可直接对接
- MemPalace：1016 的问答写入 `wing_xavier_api` 并主动召回；听筒在 `ga_api_mempalace.py`

## 故障排除

### 端口被占用
修改 `ga_api_config.json` 中的 `port` 字段后重启服务。

### 无法连接
检查防火墙，确认 `1016`（或你改过的端口）已开放；跨机时确认服务监听的是 `0.0.0.0` 而不是 `127.0.0.1`。

### API Key 错误
确认环境变量 `GA_API_KEY` 已设置，且和请求头里的 Bearer 一致。
