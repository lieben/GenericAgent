# 🚀 快速开始指南

**从零开始，5 分钟启动日记 + 梦境系统**

---

## 📋 前置检查清单

- [ ] Python 3.10 以上（检查：`python3 --version`）
- [ ] Obsidian 已安装
- [ ] 一个 Obsidian Vault（或新建一个）
- [ ] GenericAgent 或其他兼容 MCP 的 Agent 框架
- [ ] 本机可访问的 HTTP 端口 8766、8767（日记和梦境服务）

---

## 第一步：准备 Obsidian Vault

### 1.1 创建或选择一个 Vault

在 Obsidian 中打开或创建一个 Vault，建议命名为 `LifeVault` 或 `PersonalVault`。

### 1.2 创建必要的文件夹

在 Vault 根目录下创建以下文件夹结构：

```
YourVault/
├── Diary/
│   ├── 2026/
│   │   ├── 09/
│   │   └── ...
│   └── Fragments/           # 碎片日记
├── Dreams/
│   ├── 2026/
│   │   ├── 09/
│   │   └── ...
│   └── latest_context.md    # 最新上下文
└── .obsidian/              # Obsidian 配置（自动创建）
```

**快速命令**（Mac/Linux）：
```bash
mkdir -p "YourVault/Diary/2026/09"
mkdir -p "YourVault/Dreams/2026/09"
mkdir -p "YourVault/Diary/Fragments"
```

### 1.3 记下 Vault 路径

后续配置需要用到完整路径，例如：
```
/Users/username/Documents/Obsidian/LifeVault
```

---

## 第二步：解压和配置系统

### 2.1 解压交付包

```bash
unzip diary-dream-package.zip
cd diary-dream-package
```

### 2.2 配置日记系统

编辑 `config/diary_config.example.toml`，改为 `config/diary_config.toml`：

```toml
[storage]
# 改成你的 Obsidian Vault 路径
vault_path = "/Users/username/Documents/Obsidian/LifeVault"
diary_folder = "Diary"
fragments_folder = "Diary/Fragments"

[diary]
# 日期格式
date_format = "%Y-%m-%d"
# 是否启用 MemPalace 同步
enable_mempalace = false

[server]
# MCP 服务端口（保持默认或修改为其他未占用的端口）
port = 8766
host = "127.0.0.1"
```

### 2.3 配置梦境系统

编辑 `config/dream_config.example.toml`，改为 `config/dream_config.toml`：

```toml
[storage]
# 改成你的 Obsidian Vault 路径
vault_path = "/Users/username/Documents/Obsidian/LifeVault"
dreams_folder = "Dreams"

[dream]
# 每日复盘时间（24 小时制）
review_time = "22:00"
# 是否启用 MemPalace 同步
enable_mempalace = false
# 上下文字数
context_length = 4000
# 是否包含群聊内容
include_group_chats = false

[l4_sources]
# L4 原始会话存储路径（如果有）
sessions_dir = ""

[server]
# MCP 服务端口
port = 8767
host = "127.0.0.1"
```

---

## 第三步：启动 MCP 服务

### 3.1 一键启动（推荐）

```bash
bash startup/start_both.sh
```

输出应该类似：

```
📅 Diary MCP Server starting on http://127.0.0.1:8766
✅ Diary MCP ready

🌙 Dream MCP Server starting on http://127.0.0.1:8767
✅ Dream MCP ready
```

### 3.2 分别启动（如需调试）

**启动日记服务：**
```bash
bash startup/start_diary_mcp.sh
```

**启动梦境服务（新终端）：**
```bash
bash startup/start_dream_mcp.sh
```

### 3.3 验证服务是否运行

```bash
# 检查日记服务
curl http://127.0.0.1:8766/health

# 检查梦境服务
curl http://127.0.0.1:8767/health
```

返回应该包含 `"status": "ok"`。

---

## 第四步：在 Agent 中配置 MCP

### 方法 A：GenericAgent (推荐)

编辑你的 GA 配置文件（通常在 `~/.ga/config.json` 或项目目录下）：

```json
{
  "mcp_servers": [
    {
      "name": "diary",
      "enabled": true,
      "transport": "http",
      "url": "http://127.0.0.1:8766"
    },
    {
      "name": "dream", 
      "enabled": true,
      "transport": "http",
      "url": "http://127.0.0.1:8767"
    }
  ]
}
```

然后重启 GA。

### 方法 B：其他 Agent 框架

按照你的 Agent 框架的 MCP 集成文档，添加这两个 HTTP 端点即可。

---

## 第五步：开始使用

### 在 Agent 中调用工具

#### 日记工具

```python
# 写日记
result = diary.write(content="今天天气很好，在公园里待了一下午")

# 追加到最后一条日记
result = diary.append(fragment="晚上和朋友吃了火锅")

# 读最近 N 天的日记
entries = diary.read(days=7)

# 搜索日记（按关键词）
results = diary.search(keyword="开心", limit=5)

# 列出日期范围内的日记
list_result = diary.list(start_date="2026-09-01", end_date="2026-09-20")
```

#### 梦境工具

```python
# 每日复盘（通常由定时任务自动调用）
result = dream.review(date="2026-09-20")

# 查询特定日期的梦境
dream_entry = dream.get(date="2026-09-20")

# 搜索情感关键词
results = dream.search(keyword="关于未来的想象", limit=3)

# 列出最近 N 天的梦境
list_result = dream.list(days=7)

# 提取关系事实
facts = dream.extract_facts(date="2026-09-20")
```

### 示例对话

```
用户：帮我记录今天的日记

Agent: 
我来帮你记录。今天发生了什么呢？

用户：今天和朋友一起去爬山，天气很晴朗，心情很好。

Agent:
[调用 diary.write]
✅ 已记录：今天和朋友一起去爬山，天气很晴朗，心情很好。

---

用户：帮我回顾一下最近关于梦想的日记

Agent:
[调用 diary.search(keyword="梦想")]
最近你提到梦想的日记：
- 2026-09-18：关于未来职业方向的思考
- 2026-09-15：晚上做了个梦，梦到自己成为...
```

---

## 🐛 常见问题

### Q1: 启动时报错 "port already in use"

**解决方案**：
- 检查是否有其他进程占用了 8766 或 8767 端口
- 修改配置文件中的 `port` 值为其他未占用的端口
- 重新启动服务

```bash
# Mac/Linux 查看占用端口的进程
lsof -i :8766
lsof -i :8767
```

### Q2: 找不到 Obsidian Vault 路径

**解决方案**：
- 在 Obsidian 中打开 Vault，按快捷键打开文件夹
- 复制完整路径，粘贴到配置文件中
- 确保路径不包含中文空格

### Q3: Agent 无法连接到 MCP 服务

**解决方案**：
1. 验证服务是否运行：`curl http://127.0.0.1:8766/health`
2. 检查防火墙是否阻止了本地连接
3. 确认 Agent 配置中的 URL 正确
4. 尝试重启 Agent

### Q4: 日记写入失败

**检查**：
- Obsidian Vault 路径是否正确
- 本地用户是否有写权限
- Vault 是否被 Obsidian 占用（尝试关闭再重开）

查看日志：
```bash
tail -f logs/diary_mcp.log
```

---

## ✅ 验收清单

完成以下步骤后，系统应该完全就绪：

- [ ] Vault 文件夹结构已创建
- [ ] 配置文件已正确修改
- [ ] 两个 MCP 服务都在运行（`/health` 返回 200）
- [ ] Agent 已成功连接到两个服务
- [ ] 能够成功写入一条日记
- [ ] 能够搜索和读取日记
- [ ] （可选）MemPalace 已配置（见独立文档）

---

## 📖 下一步

- **[完整部署文档](DEPLOYMENT.md)** - 了解系统架构和高级配置
- **[MCP 工具参考](docs/API_REFERENCE.md)** - 所有工具的完整参数说明
- **[记忆宫殿集成](MEMPALACE_INTEGRATION.md)** - 启用长期记忆功能
- **[排查指南](docs/TROUBLESHOOTING.md)** - 深入的问题诊断

---

**准备好了？** 现在就执行第一步，创建你的 Vault 吧！ ✨
