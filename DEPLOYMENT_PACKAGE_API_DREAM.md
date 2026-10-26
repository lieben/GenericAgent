# MCP 工具参考 - 梦境系统

## dream.review - 生成情感复盘

**参数:**
- `date` (string, optional) - 日期 YYYY-MM-DD，不填则当天
- `include_group_chats` (bool, optional) - 是否包含群聊，默认 false

**返回:**
```json
{
  "success": true,
  "date": "2026-09-20",
  "review_file": "Dreams/2026/09/2026-09-20.md",
  "context_file": "Dreams/latest_context.md",
  "facts_identified": 5,
  "timestamp": "2026-09-20T22:15:00Z"
}
```

---

## dream.list - 列出梦境复盘

**参数:**
- `limit` (int, optional) - 最多返回几条，默认 10
- `since` (string, optional) - 从哪一天开始 YYYY-MM-DD

**返回:**
```json
{
  "entries": [
    {
      "date": "2026-09-20",
      "file": "Dreams/2026/09/2026-09-20.md",
      "facts_count": 3,
      "uri": "obsidian://open?path=Dreams/2026/09/2026-09-20.md"
    }
  ],
  "total": 45
}
```

---

## dream.search - 搜索情感关键词

**参数:**
- `query` (string) - 关键词（如"开心"、"失望"）
- `days` (int, optional) - 最近 N 天，默认 30

**返回:**
```json
{
  "results": [
    {
      "date": "2026-09-20",
      "context": "那天...",
      "emotion": "happy",
      "relevance": 0.92
    }
  ],
  "count": 8
}
```

---

## dream.recall - 关系记忆召回

**参数:**
- `query` (string) - 记忆查询（如"我们聊过音乐吗"）

**返回:**
```json
{
  "matches": [
    {
      "date": "2026-08-15",
      "memory": "喜欢古典音乐",
      "context": "Dreams/2026/08/2026-08-15.md"
    }
  ],
  "count": 3
}
```
