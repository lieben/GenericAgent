# Diary System

**通用生活日记外挂** - 已完成开发 ✅

零硬编码 · 本地优先 · 任意Agent可用

---

## 🎯 核心特性

- ✅ **Obsidian原生存储** - 完整Markdown日记，支持双向链接
- ✅ **第一人称叙述** - 真实生活日记，不是工作日志
- ✅ **Tips独立管理** - 正文自然，tips用于记忆召回
- ✅ **碎片日记** - 随时记录重要片段
- ✅ **Recall召回** - 自动生成可检索记忆卡片
- ✅ **MemPalace同步** - 可选记忆宫殿集成
- ✅ **零硬编码** - 不绑定任何Agent名称或角色

---

## 📊 开发完成

```
代码统计: 1270行
  - service层: 969行
  - CLI工具: 301行
  
文件统计: 23个文件
  - Python: 14
  - 文档: 6
  - 配置: 3
```

✅ 全部功能已完成并验证通过

---

## 🚀 快速开始

### 1. 配置

```bash
cd projects/diary-system
cp config.example.toml config.toml
# 编辑config.toml，配置vault_dir
```

### 2. 测试

```bash
python3 quick_test.py
```

### 3. 使用

```python
from service.storage import ObsidianStorage
from service.diary import DiaryEntry, DiaryService
from datetime import datetime

storage = ObsidianStorage(vault_dir="/path/to/vault")
service = DiaryService(storage)

entry = DiaryEntry(
    date=datetime.now(),
    content="今天的日记...",
    tips=["情感锚点：..."],
    tags=["情感"],
    importance=4
)

result = service.write_diary(entry)
```

---

## 📖 文档

- [README.md](README.md) - 项目说明与设计理念
- [AGENT_USAGE.md](AGENT_USAGE.md) - Agent使用指南（什么该写/不该写）
- [COMPLETION_REPORT.md](COMPLETION_REPORT.md) - 完整开发报告
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) - 部署说明
- [examples/usage.md](examples/usage.md) - 使用示例

---

## 🎨 设计理念

### 1. 日记不是日志

只记录"忘了会后悔、不记住会影响感情"的事，不是工作复盘。

### 2. 第一人称叙述

像本人在一天结束时写的生活日记，不是聊天记录摘录。

### 3. Tips独立

正文保持自然，tips单独章节用于记忆召回。

### 4. 本地优先

Obsidian是权威原文，MemPalace是可选索引。

### 5. 零硬编码

不绑定任何角色名称，任何Agent都可以直接使用。

---

## 📁 目录结构

```
diary-system/
├── service/           # 核心服务层
│   ├── storage.py     # Obsidian存储
│   ├── diary.py       # 日记逻辑
│   ├── fragment.py    # 碎片日记
│   ├── recall.py      # 召回卡片
│   ├── sync.py        # MemPalace同步
│   └── search.py      # 本地搜索
├── cli/               # 命令行工具
├── docs/              # 文档
├── examples/          # 示例
└── tests/             # 测试
```

---

## 🔮 第二阶段计划

- [ ] MCP Server实现
- [ ] L4 Session自动提取
- [ ] 天气API集成
- [ ] 梦境系统
- [ ] 高级搜索与可视化

---

## 📝 示例日记

可通过 MCP 或 CLI 在用户自己的 vault 中查看生成的 Markdown 日记。

---

## ⚖️ 许可

本项目供个人学习和使用，分发时请勿包含私人日记数据。

---

**当前状态**: ✅ 已完成开发，可立即投入使用
