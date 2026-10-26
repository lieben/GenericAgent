# 日记与梦境系统：Agent 部署说明

> 本文件给朋友的 Agent 使用。请严格按顺序执行。不要复制任何其他人的 `config.toml`、个人路径、会话名或密钥。

## 一、系统组成

- Diary System：从当前 Agent 的会话素材生成生活日记，可同步 MemPalace。
- Dream System：复盘指定日期的情感互动，生成梦境/复盘内容并处理事实候选。
- MCP 服务：Diary 默认 `127.0.0.1:8766`，Dream 默认 `127.0.0.1:8767`。
- Xavier API：负责执行日记和梦境定时任务；MCP 本身不负责决定业务时间。
- 桌面端：继续连接当前 Agent/Xavier API；不直接读取别人的 vault 或会话目录。

## 二、需要准备的程序

1. Python 3.9 或更高版本。
2. Git（若通过仓库获取插件）。
3. Obsidian（可选但推荐，用于查看生成的 Markdown）。
4. 能运行 Xavier API/Agent 的宿主程序。
5. MemPalace 服务地址（只有需要同步时才启用）。
6. 桌面端程序（只有需要桌面交互时安装；日记和梦境 MCP 可独立运行）。

在插件目录安装依赖：

```bash
python3 -m pip install -r projects/dream-system/requirements.txt
```

若日记系统提供单独的 `requirements.txt`，也执行：

```bash
python3 -m pip install -r projects/diary-system/requirements.txt
```

## 三、Agent 首次初始化

Agent 必须先询问或自动确认以下信息，禁止猜测、禁止使用示例中的个人值：

- `DIARY_VAULT_DIR`：用户自己的 Obsidian vault 路径。
- `DIARY_VAULT_NAME`：用户自己的 vault 名称。
- `DREAM_VAULT_DIR`：通常与日记 vault 相同，但必须由用户确认。
- `DREAM_VAULT_NAME`：同上。
- `DREAM_SESSIONS_DIR`：当前用户自己的会话素材目录。
- `DIARY_SYSTEM_DIR`：本插件目录的绝对路径。
- `MEMPALACE_SYNC_URL`：用户自己的 MemPalace 地址；未知时保持关闭，不得使用固定 IP。
- `AGENT_SESSION_ID`：当前用户 Agent 的会话标识；必须动态获取或由用户确认，不得写死任何人的会话名。

建议使用环境变量或 Agent 自己的安全配置存储，不要把密钥提交到仓库。

## 四、生成个人配置

```bash
cd <插件根目录>
cp projects/diary-system/config.example.toml projects/diary-system/config.toml
cp projects/dream-system/config.example.toml projects/dream-system/config.toml
```

将模板中的 `${...}` 替换为当前用户自己的配置变量，或保留占位符并在启动进程环境中提供变量。配置必须满足：

- 日记和梦境使用当前用户自己的 vault。
- 梦境只读取当前用户自己的会话素材目录。
- MemPalace 未确认前 `enabled = false`。
- 不复制其他人的 `config.toml`、历史目录或任务文件。

配置加载器支持 `${环境变量}` 和 `~` 展开。

## 五、启动 MCP

Agent 应使用绝对路径启动，并为每个用户维护自己的 PID/log 文件。示例：

```bash
export DIARY_VAULT_DIR="<用户自己的vault绝对路径>"
export DIARY_VAULT_NAME="<用户自己的vault名称>"
export DREAM_VAULT_DIR="$DIARY_VAULT_DIR"
export DREAM_VAULT_NAME="$DIARY_VAULT_NAME"
export DREAM_SESSIONS_DIR="<用户自己的会话素材目录>"
export DIARY_SYSTEM_DIR="<插件绝对路径>/projects/diary-system"
export MEMPALACE_SYNC_URL="<用户自己的MemPalace地址>"
export PYTHONPATH="<插件绝对路径>/projects/diary-system:<插件绝对路径>/projects/dream-system"

cd <插件绝对路径>/projects/diary-system
python3 -u mcp/diary_mcp_server.py --host 127.0.0.1 --port 8766 --config config.toml

cd <插件绝对路径>/projects/dream-system
python3 -u mcp/dream_mcp_server.py --host 127.0.0.1 --port 8767 --config config.toml
```

若端口已被占用，Agent 必须先确认占用者；可改用用户自己的端口，并同步修改 MCP 客户端配置，不能杀掉未知进程。

## 六、功能验证

启动后检查：

```bash
curl -i http://127.0.0.1:8766/mcp
curl -i http://127.0.0.1:8767/mcp
```

然后由 Agent 通过 MCP 工具完成一次非破坏性测试：

- Diary：读取/生成一条测试日记，确认文件落在当前用户的 `Diary` 目录。
- Dream：列出或读取测试范围，确认只使用当前用户素材目录。
- 检查输出文件内容和路径，禁止只依据“进程存在”判断成功。

## 七、定时任务

由 Xavier API 执行，不由 Diary/Dream MCP 自己执行：

### 每天 12:15：生活日记

处理窗口：前一自然日 `00:00:00`（含）至 `23:59:59`（含），不跨天；日记标题、文件名和归档日期使用前一自然日。

要求：

- 只处理当前用户 Agent 的会话素材。
- 技术讨论不作为生活日记。
- 生成日记并按配置同步 MemPalace。
- 任务提示使用 `${AGENT_SESSION_ID}` 或“当前 Agent 会话”，不能写死个人会话名。

### 每天 23:59：梦境/情感复盘

处理当天 `00:00:00`（含）至 `23:59:59`（含）的内容。

要求：

- 只复盘当前用户自己的素材。
- 调用 Dream System 的日复盘入口。
- 保存复盘结果，并按系统规则处理事实候选。

Agent 应为当前用户创建自己的任务文件，任务名称、报告目录和会话标识都必须用户隔离。不要直接复制别人机器上的 `sche_tasks`。

## 八、桌面端

桌面端不是日记或梦境的存储层。配置桌面端时：

1. 将 Xavier API 地址设置为当前用户自己的 API 地址。
2. 将 MCP 地址设置为当前机器实际监听的 `8766`、`8767`，或用户自定义端口。
3. 不把 vault 路径、会话素材路径或 MemPalace密钥写入桌面端前端代码。
4. 桌面端验证以“能通过 Xavier API 发送请求并收到响应”为准。
5. 日记/梦境文件路径仍由 MCP 配置控制。

## 九、隔离与安全检查

部署完成后，Agent 必须扫描自己的分发文件，确认没有出现：

- 其他用户姓名、会话 ID 或固定个人标识；
- 其他用户的 vault 名称或绝对路径；
- 其他用户的会话素材目录；
- 固定的 MemPalace 内网地址；
- 复制来的个人 `config.toml`、日志、PID 文件或历史任务报告。

允许存在的只有：

- 业务默认时间 `12:15`、`23:59`；
- 默认端口（可配置）；
- `${...}` 环境变量占位符；
- 用户自己初始化后生成的本地配置。

## 十、故障排查顺序

1. 查看 MCP 日志中的真实错误。
2. 确认配置文件存在且所有 `${...}` 变量已设置。
3. 确认 vault 和会话目录属于当前用户。
4. 确认端口占用者，不要盲目重启或杀进程。
5. 直接调用 MCP 健康检查和一个只读工具。
6. 再检查 Xavier 调度器是否运行、任务是否执行并生成报告。
7. 最后检查桌面端连接；不要用桌面端现象代替 MCP 后端验证。

## 十一、完成标准

只有以下条件全部满足，才算部署完成：

- 两个 MCP 都能实际响应调用；
- 日记和梦境写入当前用户自己的目录；
- 定时任务由当前用户的 Xavier API 执行；
- 任务不绑定任何固定个人会话名；
- MemPalace按当前用户配置同步或明确关闭；
- 桌面端能通过 Xavier API 收发请求；
- 分发目录扫描不到其他用户的个人配置和历史数据。
