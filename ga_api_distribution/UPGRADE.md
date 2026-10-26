# 从上一包升级（2026-09-21）

给已经装过 **2026-09-20 那一包**（没有 `ga_api_mempalace.py`、配置里还有 `bridge` 字段）的机器。

对方 GA：先读完本文，再向本机持有人核对；不要猜测会话 ID，不要覆盖对方自己的 `ga_api_config.json`，不要启用记忆宫殿，除非持有人明确要求。

## 这包多了什么

| 项目 | 上一包 | 这一包 |
| --- | --- | --- |
| 纯图片消息 | 会报错 | 支持。`content` 可以只有 `image_url`（data URL 或本机路径） |
| 记忆宫殿听筒 `ga_api_mempalace.py` | 无 | **可选**。默认关闭。没有自己的宫殿就不要开 |
| 桌面桥接配置 | 写在 `ga_api_config.json` 的 `bridge` | **改成环境变量**。新进程不再读 json 里的 `bridge` |

其它没变：依赖仍是 fastapi / uvicorn / pydantic；默认端口模板仍是 `8765`；鉴权仍是 `Authorization: Bearer $GA_API_KEY`。

## 升级前必做

1. 停掉正在跑的 API 进程。
2. 备份整个旧目录，至少留下：
   - `ga_api_config.json`
   - 旧的 `ga_api_server.py`
3. 从旧配置里抄出这三项（值用**这台机器自己的**，不要用别人的）：

```text
bridge.url          →  GA_DESKTOP_BRIDGE_URL
bridge.session_id   →  GA_XAVIER_SESSION_ID
bridge.session_title→  GA_XAVIER_SESSION_TITLE   （可省）
```

缺会话 ID 时服务也许能起来，但对话会打到空会话。这是本包最大的坑。

## 覆盖哪些文件

只覆盖这些，**不要整目录覆盖配置**：

| 文件 | 动作 |
| --- | --- |
| `ga_api_server.py` | 覆盖 |
| `ga_api_mempalace.py` | 新增。不接宫殿也可以放着，不会自动启用 |
| `UPGRADE.md` / `README.md` / `GA_API_DEPLOY_FOR_AGENT.md` | 覆盖文档 |
| `start_ga_api.sh` | 覆盖（会自动加载同目录 `ga_api.env`） |
| `check_ga_api.sh` / `requirements.txt` | 可覆盖，内容基本没变 |
| `ga_api_config.example.json` | 覆盖模板即可 |
| **`ga_api_config.json`** | **保留对方自己的，禁止用模板覆盖** |
| **`ga_api.env`** | 本机自己建，不要从别人那里拷 |

## 配置迁移

### 1. 环境变量（必需）

复制模板后填写本机值：

```bash
cp ga_api.env.example ga_api.env
```

`ga_api.env` 至少包含：

```bash
export GA_API_KEY='本机原来的 API Key'
export GA_DESKTOP_BRIDGE_URL='旧配置 bridge.url'
export GA_XAVIER_SESSION_ID='旧配置 bridge.session_id'
# 可选
# export GA_XAVIER_SESSION_TITLE='旧配置 bridge.session_title'
```

`./start_ga_api.sh` 会在启动前 `source ga_api.env`。如果用 LaunchAgent / systemd 长期跑，把同样的变量写进那个服务，不要只写在聊天记录里。

json 里的旧 `bridge` 段可以留着当备忘，新进程会忽略它。

### 2. 给旧 json 补一段（建议）

旧配置没有 `mempalace` 字段。本包在字段缺失时按未启用处理，不连宫殿。为避免以后误开，仍建议补上并保持关闭：

```json
"mempalace": {
  "enabled": false,
  "wing": "your_api_wing",
  "search_limit": 3,
  "max_distance": 0.85
}
```

`server` / `auth` / `model` / `safety` 沿用旧值。不要改端口、不要改模型名、不要改 `dangerous_operations_require_confirm`，除非持有人要求。

### 3. 依赖

不用重装。若环境是新的：

```bash
python3 -m pip install -r requirements.txt
```

## 启动和验证

```bash
chmod +x start_ga_api.sh check_ga_api.sh
./start_ga_api.sh
```

另开终端（端口用对方自己的，不是照抄 8765）：

```bash
./check_ga_api.sh http://127.0.0.1:<端口> "$GA_API_KEY"
```

只测「请只回复API连接测试成功」。不要用删除、关机、格式化当探针——那些句子会进桌面会话。

`/health` 里的 `session` 应等于本机 `GA_XAVIER_SESSION_ID` 对应的会话。对不上就停下来查环境变量，不要继续对第三方放行。

## 记忆宫殿（默认不要开）

听筒不是宫殿本体。包里没有 compose、数据卷、令牌。

没有自己的 MemPalace、还没给出地址和令牌、或持有人没说要接：**跳过**。API 照常工作。

只有持有人明确要接，才：

1. 保留 `ga_api_mempalace.py`，和 `ga_api_server.py` 同目录。
2. 在 `ga_api.env` 里写持有人自己的：

```bash
export MEMPALACE_MCP_URL='http://<持有人的宫殿地址>/mcp'
export MEMPALACE_MCP_HTTP_TOKEN='<持有人自己的令牌>'
```

3. `ga_api_config.json` 里 `mempalace.enabled` 改为 `true`，`wing` 改成持有人指定的分区名。
4. 重启。连不上宫殿时，问答仍走桌面，只是不写入、不召回。

不要抄别人的宫殿地址、wing 名或令牌。

## 回滚

停新进程，把备份的 `ga_api_server.py` 和 `ga_api_config.json` 拷回去，按旧方式启动。`ga_api_mempalace.py` 留下或删掉都不影响旧包。

## 不要做的事

- 不要用别人的现网 `ga_api_server.py` / `ga_api_config.json` / `ga_api_mempalace.py` 覆盖（里面会有别人的会话 ID 和宫殿地址）。
- 不要把 `ga_api.env` 发给任何人。
- 不要为了「修好」而关闭鉴权、猜会话 ID、放宽防火墙、擅自打开记忆宫殿。
