# GenericAgent API 分发包

把**对方自己正在运行的 GenericAgent 桌面会话**暴露为 OpenAI 兼容 HTTP API。不携带我的会话、密钥、聊天记录或个人配置。

> 给对方 GA：请先完整阅读 `GA_API_DEPLOY_FOR_AGENT.md`，只向本机持有人询问其中列出的配置；确认后再安装。不要猜路径、会话 ID 或密钥。

版本：2026-09-21。相对上一包：支持纯图片消息；桥接改走环境变量；多带一个记忆宫殿听筒（**仅供参考、按需使用**）。已有上一包的机器请先读 `UPGRADE.md`。

## 包含文件

| 文件 | 是否必需 | 说明 |
| --- | --- | --- |
| `ga_api_server.py` | 必需 | API 服务 |
| `ga_api_config.example.json` | 必需 | 配置模板，复制为 `ga_api_config.json` 后填写 |
| `GA_API_DEPLOY_FOR_AGENT.md` | 必需 | 给对方 GA 的部署协议 |
| `UPGRADE.md` | 从上一包升级时必需 | 升级步骤：桥接迁环境变量、补 `mempalace.enabled=false` |
| `ga_api.env.example` | 必需 | 环境变量模板，复制为 `ga_api.env` 后填写；不要把 `ga_api.env` 发给别人 |
| `start_ga_api.sh` | 必需 | 启动脚本（自动加载同目录 `ga_api.env`） |
| `check_ga_api.sh` | 必需 | 健康检查 |
| `requirements.txt` | 必需 | Python 依赖 |
| `ga_api_mempalace.py` | **参考，按需** | 连接记忆宫殿的 MCP 听筒。不是宫殿本体 |

## 最短安装流程

1. 将整个目录放到对方 GA 的 `runtime/app/ga_api_distribution/`（或保留为独立目录，不要拆散必需文件）。
2. 对方 GA 阅读 `GA_API_DEPLOY_FOR_AGENT.md` 并向持有人收集配置。
3. 复制配置模板：

```bash
cp ga_api_config.example.json ga_api_config.json
```

4. 填写 `server.host`、`server.port`、`model.id`。会话和桥接用环境变量，不要把密钥写进 json：

```bash
cp ga_api.env.example ga_api.env
# 编辑 ga_api.env：GA_API_KEY / GA_DESKTOP_BRIDGE_URL / GA_XAVIER_SESSION_ID
```

不要保留模板里的占位会话 ID，必须换成持有人自己的。从上一包升级时，把旧 `ga_api_config.json` 的 `bridge` 段抄进 `ga_api.env`，步骤见 `UPGRADE.md`。

5. 安装依赖并启动：

```bash
python3 -m pip install -r requirements.txt
chmod +x start_ga_api.sh check_ga_api.sh
./start_ga_api.sh
```

6. 另开终端验证（把端口换成配置里的值）：

```bash
./check_ga_api.sh http://127.0.0.1:8765 "$GA_API_KEY"
```

## 记忆宫殿听筒（参考，可不用）

`ga_api_mempalace.py` 只是一个打电话的听筒示例，给**已经自己建好 MemPalace** 的人参考。

- **可以不使用。** 不放这个文件、不配环境变量、把 `mempalace.enabled` 留在 `false`，API 照常工作。
- **不是宫殿本体。** 包里没有 compose、数据卷、令牌、桌面 hooks、工具 schema。那些由持有人自己的宫殿提供。
- 听筒不带默认宫殿地址。要用时由持有人自己设 `MEMPALACE_MCP_URL` 和令牌，不要照抄别人的。

若持有人确认要接入：

1. 保留 `ga_api_mempalace.py`，和 `ga_api_server.py` 放在同一目录。
2. 设置持有人自己的：

```bash
export MEMPALACE_MCP_URL='http://<持有人的宫殿地址>/mcp'
export MEMPALACE_MCP_HTTP_TOKEN='<持有人自己的令牌>'
```

3. 在 `ga_api_config.json` 里把 `mempalace.enabled` 改为 `true`，`wing` 改成持有人指定的分区名。
4. 重启 API。连不上宫殿时，问答仍会走桌面会话，只是不写入、不召回。

没有宫殿、暂时不想接、或还没拿到地址和令牌：跳过以上全部即可。

## 安全说明

- 默认监听 `0.0.0.0:8765`，跨机器使用时必须配防火墙；只在本机使用可把 `host` 改成 `127.0.0.1`。
- `/health` 和 `/v1/models` 不要求密钥；聊天接口必须使用 `Authorization: Bearer <GA_API_KEY>`。
- 不要使用模板中的示例密钥，不要把真实密钥提交到 GitHub 或发到聊天记录。
- API 是对已有桌面会话的运输层，不会替对方创建 GA，也不会替对方准备桌面桥接或记忆宫殿。
