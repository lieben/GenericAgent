## 部署说明

### 1. 安装依赖

```bash
pip install tomli
```

### 2. 配置

复制配置模板：

```bash
cp config.example.toml config.toml
```

编辑 `config.toml`，至少配置：

- `storage.vault_dir`: Obsidian Vault 根目录路径
- `agent.persona_name`: Agent 角色名（可选）
- `agent.user_name`: 用户名（可选）

### 3. 测试

在测试vault中写入一篇日记：

```bash
python3 cli/diary_cli.py write \
  --content "今天..." \
  --tips "情感锚点：..." \
  --tags "情感,日常"
```

读取今天的日记：

```bash
python3 cli/diary_cli.py read
```

### 4. Agent调用

Agent可通过CLI或直接导入service模块调用：

```python
from service.storage import ObsidianStorage
from service.diary import DiaryEntry, DiaryService
from datetime import datetime

storage = ObsidianStorage(vault_dir="/path/to/vault")
service = DiaryService(storage)

entry = DiaryEntry(
    date=datetime.now(),
    content="日记正文...",
    tips=["tip1", "tip2"],
    tags=["tag1"],
    importance=4
)

result = service.write_diary(entry)
```

### 5. MemPalace同步（可选）

配置 `config.toml` 中的 `mempalace` 部分，并在Agent中调用：

```python
from service.sync import sync_to_mempalace
from service.recall import generate_recall_card

card = generate_recall_card(...)
sync_result = sync_to_mempalace(card, wing="", room="diary")
```

**注意**: MemPalace同步需要MCP客户端实现，当前版本仅提供接口。

### 6. MCP Server（第二阶段）

MCP Server实现计划在第二阶段完成，届时可通过MCP协议远程调用所有功能。

---

## 目录结构说明

写入后的Obsidian Vault结构：

```
<vault>/
├── Diary/
│   ├── 2026/
│   │   ├── 09/
│   │   │   ├── 2026-09-10.md
│   │   │   └── 2026-09-11.md
│   │   └── 10/
│   │       └── 2026-10-01.md
│   └── Fragments/
│       ├── 2026/
│       │   └── 09/
│       │       └── 2026-09-10_1430_她说日记只记情感.md
└── .diary_sync/
    └── 2026-09-10.json
```

所有日记和碎片都可以直接在Obsidian中打开、阅读和编辑。
