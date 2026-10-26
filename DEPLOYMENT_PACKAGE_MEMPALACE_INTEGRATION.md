# 🧠 记忆宫殿 (MemPalace) 集成指南

**将梦境系统的关系事实自动上传到跨 Agent 长期记忆库**

---

## 📖 概述

梦境系统可以将每日自动提取的关系事实上传到 MemPalace 记忆宫殿，实现：

- **跨 Agent 记忆共享** - 不同 Agent 之间可以访问同一份关系记忆
- **长期事实沉淀** - 自动从日常对话中提取并记录关键关系信息
- **智能关系追踪** - 自动维护关系动态、偏好、禁忌等信息
- **情感历史重建** - 支持按时间查询关系演变过程

---

## 🔧 环保要求

### 1. MemPalace 服务必须运行

MemPalace 应该已经在你的 NAS 或本地服务器上运行。检查连通性：

```bash
# 测试 MemPalace 服务是否可达
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://192.168.1.126:8765/mcp/tools/list

# 应该返回 200 OK 和工具列表
```

### 2. 获取 MemPalace 访问令牌

在部署 MemPalace 时生成的配置文件中获取 Bearer Token，或从运行日志中查找。

---

## 🚀 启用步骤

### 第一步：配置 MemPalace 连接

编辑 `config/dream_config.toml`，修改以下部分：

```toml
[mempalace]
# 启用 MemPalace 集成
enabled = true

# MemPalace 服务地址（根据实际情况修改）
api_url = "http://192.168.1.126:8765/mcp"

# Bearer Token（从 MemPalace 配置获取）
bearer_token = "YOUR_MEMPALACE_TOKEN_HERE"

# 自动上传频率（分钟）
# 0 = 禁用自动上传，手动调用 dream.sync_facts_to_mempalace()
auto_sync_interval = 0

# 上传前确认（生产环境建议启用）
confirm_before_upload = false

# 事实置信度阈值（0-1，低于此值的事实不上传）
fact_confidence_threshold = 0.7
```

### 第二步：配置 wing（知识库分区）

梦境系统会将关系事实存储到 MemPalace 的特定 wing（分区）中。建议命名规范：

```toml
[mempalace.wings]
# 关系事实存储位置
relationships_wing = "user_relationships"

# 情感历史存储位置
emotional_history_wing = "emotional_timeline"

# 偏好与禁忌存储位置
preferences_wing = "user_preferences"
```

### 第三步：重启梦境服务

```bash
bash startup/start_dream_mcp.sh
```

检查启动日志：

```bash
tail -f logs/dream_mcp.log | grep -i mempalace
```

应该看到类似输出：

```
✅ MemPalace integration enabled
🔗 Connected to http://192.168.1.126:8765/mcp
📦 Using wings: user_relationships, emotional_timeline, user_preferences
```

---

## 📤 自动上传机制

### 何时上传

梦境系统会在以下时机自动上传事实：

1. **每日复盘完成后** - `dream.review()` 执行成功时
2. **定时同步** - 按 `auto_sync_interval` 间隔定期上传
3. **手动触发** - 调用 `dream.sync_facts_to_mempalace()`

### 上传内容

以下类型的关系事实会被上传：

```
关系事实类型
├── 偏好 (preferences)
│   ├── 食物偏好：喜欢/不喜欢的食物
│   ├── 活动偏好：喜欢的活动类型
│   └── 沟通风格：偏好的互动方式
│
├── 禁忌 (taboos)
│   ├── 不舒服的比喻或描述方式
│   ├── 不接受的行为
│   └── 敏感话题
│
├── 重要时刻 (milestones)
│   ├── 特殊日期
│   ├── 重要事件
│   └── 感情转折点
│
└── 性格特征 (traits)
    ├── 核心价值观
    ├── 性格优点/缺点
    └── 处理方式偏好
```

### 上传格式（示例）

梦境系统会将事实转换为 MemPalace 知识图谱格式：

```json
{
  "subject": "言言",
  "predicate": "likes_food",
  "object": "泰式炸鸡",
  "confidence": 0.95,
  "source": "dream_review_2026-09-20",
  "valid_from": "2026-09-20T22:00:00Z"
}
```

---

## 🔄 手动同步

### 直接调用 MCP 工具

在 Agent 中随时可以手动触发同步：

```python
# 同步所有待上传的事实
result = dream.sync_facts_to_mempalace()

# 返回示例
{
  "status": "ok",
  "uploaded": 12,
  "failed": 0,
  "summary": "✅ 同步完成: 上传12条事实"
}
```

### 查询已上传的事实

```python
# 查询 MemPalace 中关于某人的所有记录
facts = dream.query_mempalace_facts(subject="言言", limit=20)

# 按谓词类型查询
preferences = dream.query_mempalace_facts(
  subject="言言",
  predicate_type="preferences"
)
```

---

## ⚙️ 高级配置

### 自定义事实提取规则

编辑 `dream-system/service/extract.py`，修改 `FactExtractor` 类的规则：

```python
EMOTIONAL_KEYWORDS = {
    "positive": ["开心", "喜欢", "爱", "温暖", "感动"],
    "negative": ["难过", "生气", "失望", "伤心"],
    "neutral": ["想", "希望", "觉得"]
}

TABOO_PATTERNS = [
    r"不(喜欢|要|能|行).*",
    r"讨厌.*",
    r"禁忌.*",
]

PREFERENCE_KEYWORDS = {
    "food": ["吃", "喜欢吃", "不吃"],
    "time": ["喜欢", "讨厌", "偏好"],
    "interaction": ["希望", "反感", "期望"]
}
```

### 自定义 Wing 映射

如果需要将事实存储到不同的 wing，编辑 `dream-system/service/mempalace_sync.py`：

```python
WING_MAPPING = {
    "user_preferences": ["food", "activity", "communication"],
    "emotional_timeline": ["milestones", "relationship_changes"],
    "user_profile": ["traits", "values", "boundaries"],
    "interaction_history": ["conversations", "patterns"]
}
```

---

## 🔍 查询与回忆

### 从 MemPalace 检索信息

在其他 Agent 或工具中调用 MemPalace 的查询接口：

```python
# 查询某个人的所有偏好
import requests

headers = {"Authorization": "Bearer YOUR_TOKEN"}
response = requests.post(
    "http://192.168.1.126:8765/mcp",
    json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "mempalace_kg_query",
        "params": {
            "entity": "言言",
            "direction": "outgoing"
        }
    },
    headers=headers
)

facts = response.json()["result"]
# 返回言言的所有出边（关系）
```

### 时间范围查询

```python
# 查询某个时间段内新增的事实
response = requests.post(
    "http://192.168.1.126:8765/mcp",
    json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "mempalace_kg_query",
        "params": {
            "entity": "言言",
            "as_of": "2026-09-20T22:00:00Z"
        }
    },
    headers=headers
)
```

---

## 🛡️ 隐私与安全

### 哪些信息会上传

✅ **会上传**：
- 日期、时间信息
- 食物、活动偏好
- 沟通风格
- 公开性格特征

❌ **不会上传**：
- 完整对话内容
- 个人隐私细节
- 位置信息
- 财务信息

### 访问控制

- MemPalace 中的所有数据都通过 Bearer Token 保护
- 建议定期更换 Token
- 支持为不同 Agent 创建不同权限的 Token

### 数据导出与删除

```python
# 导出所有关于某人的事实
export_data = dream.export_mempalace_facts(subject="言言")

# 删除某条特定的事实
result = dream.delete_mempalace_fact(fact_id="fact_123")

# 清空某个 wing 中的所有数据
result = dream.clear_mempalace_wing(wing="user_relationships")
```

---

## 🐛 排查

### MemPalace 连接失败

```bash
# 检查服务是否运行
curl http://192.168.1.126:8765/mcp/status

# 检查防火墙和网络连通性
ping 192.168.1.126
telnet 192.168.1.126 8765

# 检查 Token 是否有效
curl -H "Authorization: Bearer YOUR_TOKEN" \
  http://192.168.1.126:8765/mcp/tools/list
```

### 事实上传失败

查看日志：
```bash
tail -f logs/dream_mcp.log | grep -i "mempalace\|upload\|error"
```

常见错误：
- **401 Unauthorized** - Token 无效或过期
- **404 Not Found** - Wing 不存在，检查配置
- **500 Server Error** - MemPalace 服务故障，联系管理员

### 数据一致性

如果发现 MemPalace 中的数据不一致，可以手动触发重新同步：

```python
# 强制全量同步（删除并重新上传所有事实）
result = dream.full_resync_to_mempalace(dry_run=True)
# 检查预期的操作
if result["status"] == "ok":
    result = dream.full_resync_to_mempalace(dry_run=False)
```

---

## 📊 监控与日志

### 同步日志

梦境系统会在 `logs/dream_mempalace_sync.log` 中记录所有同步操作：

```
2026-09-20T22:15:30 | ✅ Upload started | facts=12
2026-09-20T22:15:31 | ✅ Fact uploaded | subject=言言, predicate=likes_food, object=泰式炸鸡
...
2026-09-20T22:15:35 | ✅ Upload completed | duration=5s
```

### 监控面板（可选）

如果启用了 MemPalace 的 Web UI，可以访问：

```
http://192.168.1.126:8765/mempalace_ui/
```

查看实时的知识图谱状态、wing 信息、同步历史等。

---

## ✨ 最佳实践

1. **定期审查** - 每周检查一次自动提取的事实，确保准确性
2. **手动补充** - 对于自动提取遗漏的重要信息，手动添加
3. **版本迭代** - 当关系发展时，更新既有的事实（使用 `valid_to` 标记过期）
4. **跨 Agent 同步** - 如果有多个 Agent，定期从 MemPalace 拉取更新
5. **备份** - 定期导出 MemPalace 中的关键数据

---

## 📚 相关文档

- **[MemPalace 官方文档](https://mempalace.example.com/docs)** - 完整 API 参考
- **[梦境系统 API](docs/API_REFERENCE.md)** - 梦境系统的所有工具
- **[隐私政策](PRIVACY.md)** - 数据隐私保护说明

---

**准备好集成 MemPalace 了吗？** 按照第一步开始配置吧！✨
