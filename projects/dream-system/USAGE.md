# Dream System - 使用指南

## 快速开始

### 1. 配置

复制配置模板：
```bash
cp config.example.toml config.toml
```

编辑 `config.toml`，至少配置：
```toml
[storage]
vault_dir = "~/Documents/Obsidian/MyVault"  # 你的Obsidian vault路径
vault_name = "MyVault"                       # vault名称

[collector]
l4_sessions_dir = "~/memory/L4_raw_sessions" # L4会话目录（可选）
diary_system_dir = "~/diary-system"          # 日记系统目录（可选）
```

### 2. 每日复盘

```bash
./cli/dream_cli.py review --date 2026-09-10
```

自动完成：
- 收集当天L4会话、日记片段
- 情感内容过滤（技术讨论自动跳过）
- 生成复盘保存到 `Dreams/2026/09/2026-09-10.md`
- 提取事实候选保存到 `user_fact_candidates.md`
- 更新最近上下文到 `latest_context.md`

### 3. 召回查询

```bash
./cli/dream_cli.py recall --keywords "牛奶 饮食"
```

从历史复盘中检索相关记忆。

### 4. 列出所有复盘

```bash
./cli/dream_cli.py list
./cli/dream_cli.py list --start 2026-09-01 --end 2026-09-30
```

---

## 工作原理

### 素材收集优先级

1. **日记系统** - 如果当天写过日记，优先使用
2. **L4会话** - 从原始会话中收集互动
3. **混合模式** - 日记+L4补充（推荐）

### 智能过滤规则

**情感关键词**（高权重保留）：
```
记住、别忘、后悔、重要、在乎、喜欢、不喜欢、讨厌、
害怕、难过、委屈、安心、安全感、陪伴、边界、雷区、
不要、希望、以后、承诺、关系、日记、梦
```

**技术关键词**（自动跳过）：
```
python、脚本、报错、traceback、debug、git、commit、
接口、api、数据库、sql、代码、函数、bug、测试
```

**群聊标记**（默认排除）：
```
群、group、群里、群聊、正常人类聚集地
```

**事实标记**（自动提取）：
```
我喜欢、我不喜欢、我希望、我讨厌、不要、以后、
你要记住、必须记住、雷区、偏好
```

### 输出文件

**梦境复盘** (`Dreams/YYYY/MM/YYYY-MM-DD.md`)：
```markdown
---
date: 2026-09-10
created: 2026-09-10T22:30:15
source: diary+l4
---

# 2026-09-10 情感复盘

## 上下文
[筛选后的情感互动内容]

## 事实候选
1. 我希望你记住，我不喜欢温牛奶。
```

**事实候选** (`user_fact_candidates.md`)：
```markdown
# User Fact Candidates

## 2026-09-10

我希望你记住，我不喜欢温牛奶。
```

**最近上下文** (`latest_context.md`)：
```markdown
# Latest Context (最近4000字)

[2026-09-10 10:30] user: 我希望你记住我不喜欢温牛奶
[2026-09-10 10:31] assistant: 好的，我记住了
...
```

---

## 与MemPalace集成（可选）

在 `config.toml` 中启用：
```toml
[mempalace]
auto_sync = true
api_url = "http://localhost:8520"
dream_drawer = "情感复盘"
fact_drawer = "关系事实"
```

每次复盘后自动同步到MemPalace，供Agent主动召回。

---

## 隐私与安全

- ✅ 完全本地存储，不依赖云服务
- ✅ 敏感信息自动标记
- ✅ 技术内容自动排除
- ✅ 群聊默认不进入复盘

---

## 常见问题

**Q: 为什么技术讨论被跳过了？**  
A: 梦境系统专注情感关系复盘，技术内容默认过滤。如需保留，修改 `config.toml` 中的 `tech_keywords`。

**Q: 可以包含群聊内容吗？**  
A: 可以。在 `config.toml` 中设置 `include_group = true`。

**Q: 事实候选如何确认为正式事实？**  
A: 人工审阅 `user_fact_candidates.md`，确认后移动到 `user_fact_memory.md`。

**Q: L4找不到怎么办？**  
A: 梦境系统优先使用日记和结构化记忆，L4只作兜底。如果日记系统已记录，无需L4。

---

## 开发与扩展

梦境系统设计为零硬编码，任意Agent可用：

```python
from dream_system.service import DreamService

service = DreamService(config_path="config.toml")
result = service.review_day(datetime(2026, 9, 10))
print(result.uri)  # obsidian://...
```

详见 `170_ANALYSIS.md` 了解完整架构。
