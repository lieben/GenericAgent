# MCP 工具参考 - 日记系统

## diary.write - 写日记

**参数:**
- `date` (string, optional) - 日期 YYYY-MM-DD，不填则当天
- `content` (string) - 日记正文
- `tips` (string, optional) - 附加 tips 用于记忆召回

**返回:**
```json
{
  "success": true,
  "file": "Diary/2026/09/2026-09-20.md",
  "timestamp": "2026-09-20T14:30:00Z"
}
```

---

## diary.read - 读日记

**参数:**
- `date` (string) - 日期 YYYY-MM-DD

**返回:**
```json
{
  "content": "今天...",
  "tips": "记忆关键词",
  "file": "Diary/2026/09/2026-09-20.md"
}
```

---

## diary.search - 搜索日记

**参数:**
- `query` (string) - 关键词
- `days` (int, optional) - 最近 N 天，默认 30

**返回:**
```json
{
  "results": [
    {
      "date": "2026-09-20",
      "excerpt": "...",
      "relevance": 0.95
    }
  ],
  "count": 5
}
```

---

## diary.list - 列出日记

**参数:**
- `since` (string, optional) - 从哪一天开始 YYYY-MM-DD
- `limit` (int, optional) - 最多返回几条，默认 20

**返回:**
```json
{
  "entries": [
    {
      "date": "2026-09-20",
      "title": "今天很开心",
      "file": "Diary/2026/09/2026-09-20.md"
    }
  ],
  "total": 120
}
```
