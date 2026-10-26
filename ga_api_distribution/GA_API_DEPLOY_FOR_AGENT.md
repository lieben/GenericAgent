# 给对方 GA 的部署协议

你是接收此分发包的 GenericAgent。请先读完本文件，再向本机持有人询问配置；不要猜测、不要索取我的密钥或路径，也不要复制其他人的配置。

## 先向持有人确认这几项

请逐项询问并等待明确回答：

1. **安装目录**：是否将整个分发目录放在本机 GA 的 `runtime/app/ga_api_distribution/`？如果实际目录不同，请确认绝对路径。
2. **监听方式**：API 只给本机使用，还是要让同一局域网其他设备访问？本机使用填 `127.0.0.1`；跨设备才填 `0.0.0.0`，并确认端口（模板默认 `8765`）。
3. **模型 ID**：对外暴露的模型名是否使用 `xavier-gemini-pro`，还是持有人指定的其它名字。
4. **桌面桥接**：本机 GA 桌面桥接的 URL（常见 `http://127.0.0.1:14168`）以及**真实会话 ID**。不得猜测，不得沿用脚本里的默认值。
5. **API Key**：由持有人在本机生成随机字符串，通过环境变量 `GA_API_KEY` 提供。不要写入 json、脚本或聊天记录。
6. **记忆宫殿（可选）**：默认**不接入**。只有持有人明确说要接，才继续问宫殿 MCP 地址、令牌、wing 名。没有宫殿就跳过，不要启用。

## 配置文件

```bash
cp ga_api_config.example.json ga_api_config.json
```

在 `ga_api_config.json` 中填写持有人刚确认的：

- `server.host` / `server.port`
- `model.id`
- `mempalace.enabled`：没有明确要求接入时保持 `false`

会话和桥接不要写进 json。复制环境变量模板后填写：

```bash
cp ga_api.env.example ga_api.env
```

`start_ga_api.sh` 会自动加载同目录 `ga_api.env`。如果需要长期运行，应由持有人自己的系统服务/启动器管理同样的变量。

若这台机器已经装过上一包（配置里还有 `bridge` 字段），改读 `UPGRADE.md`，不要用模板覆盖现有 `ga_api_config.json`。

## 记忆宫殿听筒是参考文件

`ga_api_mempalace.py` **可以按需使用，不是安装必需品**。

- 它只是连接 MemPalace 的 MCP 听筒示例，不是宫殿本身。
- 包不提供 compose、数据卷、令牌、桌面插件。
- 持有人没有宫殿、或未给出地址和令牌：不要改 `enabled`，可以不加载该文件。
- 只有持有人确认接入时，才保留该文件、设置持有人自己的 `MEMPALACE_MCP_URL` / `MEMPALACE_MCP_HTTP_TOKEN`，并把 `mempalace.enabled` 改为 `true`。不要照抄别人的地址或令牌。

## 安装与启动

```bash
python3 -m pip install -r requirements.txt
chmod +x start_ga_api.sh check_ga_api.sh
./start_ga_api.sh
```

启动前检查：

- `ga_api_config.json` 存在；
- `GA_API_KEY`、`GA_DESKTOP_BRIDGE_URL`、`GA_XAVIER_SESSION_ID` 已按持有人确认值设置；
- 本机 GA 桌面会话和桥接服务确实在运行；
- 端口未被其他程序占用；
- 若跨设备访问，防火墙只允许持有人指定的网络范围；
- 未确认接入记忆宫殿时，`mempalace.enabled` 为 `false`。

## 验证顺序

另开终端执行：

```bash
./check_ga_api.sh http://127.0.0.1:<端口> "$GA_API_KEY"
```

依次确认 `/health`、`/v1/models` 和带 Bearer 鉴权的 `/v1/chat/completions`。测试内容只使用“请只回复API连接测试成功”，不要发送真实隐私内容。

## 失败时的处理

只报告真实错误和已核对的路径、端口、进程状态。不要为了“修好”而关闭鉴权、猜测会话 ID、复制别人的密钥、放宽防火墙、擅自打开记忆宫殿，或修改本机 GA 核心文件。若发现 API 服务与本机桥接接口不兼容，停止安装并向持有人说明需要提供桥接接口信息或进行适配。

## 完成回报

部署完成后向持有人回报：实际安装目录、监听地址和端口、模型 ID、鉴权是否开启、记忆宫殿是否启用（不要回报令牌）、检查脚本结果；绝不回报真实 API Key。
