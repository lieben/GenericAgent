# 📦 日记 + 梦境系统完整交付包

**通用生活日记 + 情感复盘系统** - 零硬编码，任意 Agent 可用

---

## 📋 包含内容

```
diary-dream-package/
├── README.md                          # 本文件
├── QUICK_START.md                     # 快速开始指南
├── DEPLOYMENT.md                      # 完整部署说明
├── MEMPALACE_INTEGRATION.md           # 记忆宫殿接入指南
├── startup/
│   ├── start_diary_mcp.sh            # 日记 MCP 启动脚本
│   ├── start_dream_mcp.sh            # 梦境 MCP 启动脚本
│   ├── start_both.sh                 # 同时启动两个服务
│   └── env.example                   # 环境变量示例
├── config/
│   ├── diary_config.example.toml     # 日记系统配置模板
│   ├── dream_config.example.toml     # 梦境系统配置模板
│   └── README.md                     # 配置说明
├── diary-system/                      # 日记系统源码
│   ├── service/                       # 核心服务
│   ├── mcp/diary_mcp_server.py       # MCP 服务器
│   └── ...
├── dream-system/                      # 梦境系统源码
│   ├── service/                       # 核心服务
│   ├── mcp/dream_mcp_server.py       # MCP 服务器
│   └── ...
└── docs/
    ├── ARCHITECTURE.md                # 系统架构说明
    ├── API_REFERENCE.md               # MCP 工具参考
    └── TROUBLESHOOTING.md             # 常见问题排查
```

---

## 🚀 五分钟快速启动

### 前置要求

1. **Python 3.10+**
2. **Obsidian** - 用于存储日记和梦境
3. **GenericAgent** (GA) - Agent 运行环境
4. **MemPalace** (可选) - 用于长期记忆

### 第一步：配置 Obsidian Vault

```bash
# 1. 在 Obsidian 中创建一个 Vault，命名如 "MyHomeVault"
# 2. 在 Vault 根目录下创建以下文件夹：
#    - Diary/      （日记存储）
#    - Dreams/     （梦境存储）

# 3. 修改配置文件中的 vault_path
```

### 第二步：启动 MCP 服务

```bash
# 1. 解压交付包
unzip diary-dream-package.zip
cd diary-dream-package

# 2. 配置环境变量（可选）
cp startup/env.example .env
# 根据需要编辑 .env

# 3. 启动两个服务
bash startup/start_both.sh

# 或者分别启动：
# bash startup/start_diary_mcp.sh   # 启动日记 MCP (port 8766)
# bash startup/start_dream_mcp.sh   # 启动梦境 MCP (port 8767)
```

### 第三步：在 GenericAgent 中注册 MCP

在你的 GA 实例中添加以下配置：

```json
{
  "mcp_servers": [
    {
      "name": "diary",
      "command": "python",
      "args": ["diary-system/mcp/diary_mcp_server.py"],
      "transport": "http",
      "url": "http://127.0.0.1:8766"
    },
    {
      "name": "dream",
      "command": "python",
      "args": ["dream-system/mcp/dream_mcp_server.py"],
      "transport": "http",
      "url": "http://127.0.0.1:8767"
    }
  ]
}
```

### 第四步：开始使用

在 GA 中调用工具：

```python
# 日记系统
diary.write(content="今天很开心")           # 写日记
diary.read(days=7)                        # 读最近7天
diary.search(keyword="开心")              # 搜索

# 梦境系统
dream.review(date="2026-09-20")           # 每日复盘
dream.recall(keyword="关系")              # 情感回忆
dream.list(days=7)                        # 列出最近梦境
```

---

## 📖 详细文档

- **[快速开始指南](QUICK_START.md)** - 新手入门
- **[完整部署说明](DEPLOYMENT.md)** - 生产环境部署
- **[MCP 工具参考](docs/API_REFERENCE.md)** - 所有可用工具的详细说明
- **[记忆宫殿集成](MEMPALACE_INTEGRATION.md)** - 如何接入 MemPalace 长期记忆
- **[系统架构](docs/ARCHITECTURE.md)** - 系统设计与数据流
- **[排查指南](docs/TROUBLESHOOTING.md)** - 常见问题与解决方案

---

## 🔧 核心特性

### 日记系统 (Diary)
- ✅ **Obsidian 原生存储** - 完整 Markdown 日记，支持双向链接
- ✅ **第一人称叙述** - 真实生活日记，不是工作日志
- ✅ **碎片日记** - 随时记录重要片段
- ✅ **Recall 召回** - 自动生成可检索记忆卡片
- ✅ **MemPalace 同步** - 可选记忆宫殿集成

### 梦境系统 (Dream)
- ✅ **每日自动复盘** - 基于当天会话和日记的情感复盘
- ✅ **智能过滤** - 情感内容高权重，技术讨论自动跳过
- ✅ **事实提取** - 自动识别关键关系信息
- ✅ **记忆召回** - 支持情感和关系数据快速查询
- ✅ **最新上下文** - 自动维护近期 4000 字对话背景

---

## 🌐 MemPalace 集成

梦境系统可以自动将提取的关系事实上传到 MemPalace 记忆宫殿，实现跨 Agent 的长期记忆。

详见 **[记忆宫殿集成指南](MEMPALACE_INTEGRATION.md)**

---

## 💡 常见问题

**Q: 支持多个 Agent 同时使用吗？**
A: 是的。系统零硬编码，只需复制给任意 Agent 即可使用。

**Q: Obsidian 是必需的吗？**
A: 推荐使用，但如果修改 `vault_path` 配置，也可以使用其他存储后端。

**Q: 可以关闭 MemPalace 集成吗？**
A: 可以。在配置文件中设置 `enable_mempalace = false` 即可。

更多问题见 **[排查指南](docs/TROUBLESHOOTING.md)**

---

## 📝 许可与使用

这是一个**通用插件**，设计用于任意 Agent 的日常生活记录与情感分析。

- 所有数据存储在本地 Obsidian Vault
- 不涉及任何个人隐私上传（除非显式启用 MemPalace）
- 完全开源，可自由修改和扩展

---

## 📞 技术支持

遇到问题？

1. 先查看 **[排查指南](docs/TROUBLESHOOTING.md)**
2. 检查日志文件：`logs/diary_mcp.log` 和 `logs/dream_mcp.log`
3. 验证配置文件是否正确（见 `config/` 目录）

---

**祝你使用愉快！** ✨

最后更新：2026-09-20
