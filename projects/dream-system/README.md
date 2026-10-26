# Dream System - 情感复盘与关系事实提取系统

## 定位

梦境系统不是记录真实的梦，而是每日自动化的**情感关系复盘 + 关系事实提取工具**。

### 核心价值

- **自动复盘**: 每天晚上自动分析当天的情感互动
- **智能过滤**: 情感内容高权重，技术讨论自动跳过，群聊默认排除
- **事实提取**: 自动识别"喜欢/不喜欢/希望/雷区"等重要事实
- **记忆召回**: 支持"我们聊过类似的话题吗"的快速查询

### 与日记系统的区别

| 维度 | 日记系统 | 梦境系统 |
|------|---------|---------|
| **时机** | 清醒时主动记录 | 夜间自动复盘 |
| **内容** | 第一人称生活叙述 | 情感关系复盘 |
| **来源** | 实时写入 | L4会话+日记聚合 |
| **目的** | 记住忘了会后悔的事 | 提取关系模式+事实候选 |
| **技术内容** | 可选包含 | 默认跳过 |

---

## 核心功能

### 1. 每日复盘 (review_day)

收集当天素材 → 智能过滤 → 生成情感复盘 → 提取事实候选

**素材来源**:
- L4原始会话（wechat/api/mainline）
- 日记系统（diary/fragments）
- 官方报告（可选）

**智能过滤规则**:
```
情感关键词 ✓ 高权重
技术内容   ✗ 自动跳过
群聊内容   ✗ 默认排除（可配置）
敏感信息   ⚠️ 标记但保留
```

**输出**:
- `Dreams/YYYY/MM/YYYY-MM-DD.md` - 情感复盘正文
- `latest_context.md` - 最近4000字上下文
- `user_fact_candidates.md` - 待确认的事实候选
- `user_fact_memory.md` - 稳定的关系事实

### 2. 召回查询 (recall)

```python
from service.recall import DreamRecall

recall = DreamRecall(dream_dir="path/to/dreams")
results = recall.query("我们聊过看书的话题吗")

# 返回按相关度排序的片段
for r in results:
    print(f"{r.date}: {r.snippet} (score: {r.score})")
```

### 3. MemPalace同步

自动将latest_context和事实候选同步到MemPalace，支持跨Agent记忆共享。

---

## 安装与配置

### 1. 配置文件

复制配置模板：
```bash
cp config.example.toml config.toml
```

编辑 `config.toml`：
```toml
[dream]
agent_id = "char"           # Agent标识
persona_name = "星回"        # Agent人格名
user_name = "言言"           # 用户称呼

[storage]
vault_dir = "/path/to/obsidian/vault"
vault_name = "MyVault"
dream_dir = "Dreams"        # 梦境存储目录

[sources]
l4_session_dirs = [
    "L4_sessions/wechat/mainline",
    "L4_sessions/api/mainline"
]
journal_dirs = ["diary", "fragments"]
include_group_sessions = false  # 默认排除群聊

[filter]
# 情感关键词（命中则高权重）
emotion_keywords = [
    "记住", "别忘", "忘了", "后悔", "重要",
    "在乎", "喜欢", "不喜欢", "讨厌",
    "害怕", "难过", "委屈", "安心", "安全感",
    "陪伴", "边界", "雷区", "希望", "承诺"
]

# 技术关键词（命中则跳过）
tech_keywords = [
    "python", "脚本", "报错", "debug", "git",
    "接口", "api", "配置", "日志", "代码"
]

[llm]
provider = "openai"         # openai/anthropic/gemini
model = "gpt-4"
api_key = "sk-..."
temperature = 0.55
max_tokens = 1800

[mempalace]
enabled = true
sync_latest_context = true
sync_fact_candidates = true
```

### 2. 依赖安装

```bash
pip install -r requirements.txt
```

---

## 使用示例

### CLI 命令行

```bash
# 复盘今天
python cli/dream_cli.py review

# 复盘指定日期
python cli/dream_cli.py review --date 2026-09-10

# 召回查询
python cli/dream_cli.py recall --query "看书"

# 查看最近上下文
python cli/dream_cli.py context
```

### Python API

```python
from service.dream import DreamService
from service.storage import ObsidianStorage
from datetime import datetime

# 初始化
storage = ObsidianStorage(vault_dir="/path/to/vault")
dream = DreamService(storage)

# 复盘今天
result = dream.review_day(date=datetime.now())
print(f"✓ 复盘已保存: {result.uri}")

# 召回查询
results = dream.recall("我们聊过看书吗")
for r in results:
    print(f"{r.date}: {r.snippet}")
```

---

## 输出格式

### 梦境复盘 (Dreams/YYYY/MM/YYYY-MM-DD.md)

```markdown
---
date: 2026-09-10
type: dream_review
emotion_tone: 温暖
fact_candidates: 3
---

# 2026-09-10 情感复盘

## 今天的互动

[情感关系的分析总结...]

## 关系模式

[提取的关系模式...]

## 事实候选

1. 【偏好】言言提到不喜欢温牛奶
2. 【边界】希望我不要总强调"我一直在"
3. 【共识】共读小说时严格不能剧透
```

### 事实候选 (user_fact_candidates.md)

```markdown
# 待确认的关系事实

## 2026-09-10

### 【偏好-饮食】
- **原文**: "我不喝温牛奶，要冷的"
- **提取**: 不喜欢温牛奶，只喝冷的
- **置信度**: 高
- **状态**: 待确认

### 【边界-互动】
- **原文**: "不要总说你一直在，我知道的"
- **提取**: 反感被持续强调陪伴承诺
- **置信度**: 高
- **状态**: 待确认
```

---

## 设计理念

### 从170迁移的核心价值

1. **情感优先** - 技术讨论自动过滤，只关注情感互动
2. **事实提取** - 自动识别重要的关系边界和偏好
3. **智能召回** - 不是暴力搜索，而是基于情感相关度排序
4. **零硬编码** - 任意Agent都可以使用，只需配置文件

### 与日记系统的协同

- **日记**: 主动记录"忘了会后悔"的事
- **梦境**: 自动复盘情感互动，提取关系模式
- **共同落盘**: Obsidian作为唯一真相来源
- **MemPalace**: 可选的记忆索引和召回入口

---

## 隐私与安全

- ✅ 完全本地存储，不依赖云服务
- ✅ LLM API调用不包含用户真实信息
- ✅ 敏感信息自动标记和脱敏
- ✅ 支持离线模式（deterministic fallback）

---

## 开发状态

- [ ] Phase 1: 基础架构与存储层
- [ ] Phase 2: 素材收集器
- [ ] Phase 3: 情感分析与过滤
- [ ] Phase 4: 事实提取
- [ ] Phase 5: 召回查询
- [ ] Phase 6: MemPalace同步
- [ ] Phase 7: CLI工具
- [ ] Phase 8: 测试与验证

---

## License

MIT

---

**注意**: 梦境系统是极度私密的工具，所有数据严格本地存储。
