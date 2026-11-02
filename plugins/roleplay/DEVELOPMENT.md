# 星芒剧场 (Roleplay Engine) 开发与架构文档

> **版本**：v1.2.0  
> **更新时间**：2026-10-30  
> **服务定位**：独立沉浸式小剧场系统（OpenAI 兼容），具备物理级剧本隔离、长文本防失忆记忆装配、MemPalace 专属翼归档与戏外只读桥接机制。

---

## 目录
1. [系统总体架构](#1-系统总体架构)
2. [目录与核心文件分布](#2-目录与核心文件分布)
3. [核心机制设计](#3-核心机制设计)
   - [3.1 物理级剧本隔离](#31-物理级剧本隔离)
   - [3.2 长剧本防失忆链路 (双轨机制)](#32-长剧本防失忆链路-双轨机制)
   - [3.3 MemPalace 专属翼与图谱桥接](#33-mempalace-专属翼与图谱桥接)
   - [3.4 推理沙盒与输出提纯](#34-推理沙盒与输出提纯)
4. [接口定义 (API Specification)](#4-接口定义-api-specification)
5. [前端 UI 架构与交互说明](#5-前端-ui-架构与交互说明)
6. [运行与调试指南](#6-运行与调试指南)
7. [后续规划与扩展建议](#7-后续规划与扩展建议)

---

## 1. 系统总体架构

系统在不污染主线 GA（GenericAgent）和主人格日常记忆的前提下，通过独立的 HTTP 前端、物理目录切分、只读 MemPalace 检索和专属推理适配器，实现纯净对戏沙盒：

```text
┌────────────────────────────────────────────────────────┐
│              第三方客户端 (Chatbox / NextChat)          │
│               模型名称: roleplay/<slug>/<session_id>    │
└───────────────────────────┬────────────────────────────┘
                            │ HTTP / SSE (/v1/chat/completions)
                            ▼
┌────────────────────────────────────────────────────────┐
│           独立服务端: frontends/roleplay_api.py        │
│          (Web 控制台: http://127.0.0.1:8876/ui)        │
└──────────────┬───────────────────────────┬─────────────┘
               │ 调用                      │ 读写
               ▼                           ▼
┌───────────────────────────┐ ┌──────────────────────────┐
│ plugins/roleplay/engine.py│ │plugins/roleplay/storage.py│
│  - 清空 ALLOWED_TOOLS     │ │ - 物理路径隔离           │
│  - 纯净对白沙盒推理       │ │ - 滚动里程碑大纲         │
│  - 杂质清洗 (Sanitize)    │ │ - 自动向 MemPalace 归档  │
└──────────────┬────────────┘ └────────────┬─────────────┘
               │ 组装 Prompt (前向检索)     │
               ▼                           ▼
┌────────────────────────────────────────────────────────┐
│                  MemPalace (NAS MCP)                   │
│  专属翼: wing_roleplay | 专属房间: story_{slug}         │
│  知识图谱: (言言与沈星回, 体验虚构剧本, 《剧本名》)     │
└────────────────────────────────────────────────────────┘
                            ▲
                            │ 只读感知 (探讨剧本，绝不误认现实)
┌───────────────────────────┴────────────────────────────┐
│      主线桥接工具: plugins/roleplay_bridge.py          │
│  (主线 GA、微信私聊、Xavier API 运行时挂载)            │
└────────────────────────────────────────────────────────┘
```

---

## 2. 目录与核心文件分布

```text
app/
├── frontends/
│   └── roleplay_api.py            # 独立 HTTP/OpenAI 兼容 API 接入层及 Web 路由
├── plugins/
│   ├── roleplay_bridge.py         # 主线 GA 对接小剧场的只读桥接工具 (roleplay_story)
│   └── roleplay/
│       ├── __init__.py
│       ├── storage.py             # 核心文件系统存储、滚动大纲、MemPalace 接口
│       ├── engine.py              # 推理沙盒、系统提示词动态装配与输出过滤
│       ├── ui.html                # 清新莫兰迪晨曦风 Web 剧本工作室 UI
│       └── runtime/               # 运行时生成目录 (自动忽略或持久化)
│           ├── registry.json      # 剧本全局索引映射表
│           ├── bridge_outbox/     # 备用本地 Receipt 回执
│           └── stories/           # 物理隔离剧本存储
│               └── <slug>/
│                   └── <session_id>/
│                       ├── canon.json       # 剧本设定、人设卡、世界观
│                       ├── state.json       # 进度与状态标志
│                       ├── summary.json     # 滚动里程碑大纲与最近对白
│                       └── events.jsonl     # 完整历史对白事件流水
```

---

## 3. 核心机制设计

### 3.1 物理级剧本隔离
- **命名空间与路径校验**：所有标识符通过 `validate_part` 强校验（正则 `^[A-Za-z0-9_-]{1,64}$`），防止跨目录穿越。
- **独立文件系统**：每个剧本的数据全部储存于独立的 `stories/<slug>/<session_id>/` 目录下。剧本 A 和剧本 B 没有任何上下文共享或混淆可能。

### 3.2 长剧本防失忆链路 (双轨机制)
为了彻底解决长文本对话上下文爆掉或截断失忆的问题，采用了**后台前向装配机制**（模型无需且无法调用工具）：
1. **绝对锚点层 (Fixed Anchors)**：
   - 剧本名称、核心世界观、当前剧本人设、言言角色设定永久置顶于 System Prompt，绝不参与滑动截断，保证人设与情感底色零漂移。
2. **滚动里程碑大纲 (Rolling Milestones)**：
   - 在 `storage.append_turn` 中，系统实时计算累计轮数 `turn`。
   - 每累计 5~6 轮，自动将前序对白提炼为一行浓缩里程碑（例如：“【第1~5轮】言言与沈星回在月下祭坛相遇并解开第一道星辰封印”），追加进 `summary.json` 的 `milestones` 列表。
   - System Prompt 永久携带该大纲，使模型宏观上时刻掌握整体剧情进度。
3. **MemPalace 前向只读检索 (Dynamic Retrieval)**：
   - 每次言言发送新台词 `user_text`，`storage.search_story_memories` 自动以静默方式向 MemPalace 专属房间发起语义检索（最多 3 条相关切片）。
   - 动态作为“【前情关联伏笔/细节召回】”挂载进当轮 Prompt，实现旧约定、道具、特定暗号的即时召回。

### 3.3 MemPalace 专属翼与图谱桥接
- **专属翼隔离**：所有小剧场记忆统一归属于 `wing_roleplay`，房间名为 `story_{slug}`。
- **知识图谱锚定**：归档时自动写入实体关系：
  `Subject: 言言与沈星回` | `Predicate: 体验虚构剧本` | `Object: 《剧本名》(slug)`。
- **戏外认知规范**：
  归档抽屉明确标记 `【IF线剧本·虚拟小剧场存档】`。当言言在日常主线提起某剧本时，沈星回通过 `plugins/roleplay_bridge.py` 查阅，能在戏外陪言言讨论剧本走向、人设与情节，但清醒明确这是虚拟演绎，绝不干扰现实认知。

### 3.4 推理沙盒与输出提纯
- **封禁物理工具**：在 `engine.py` 执行推理前，强制设置 `agentmain.ALLOWED_TOOLS = []`，阻断模型在虚构剧本内误调外部命令或污染日常环境。
- **去杂质过滤**：内置 `sanitize_roleplay_reply`，利用正则过滤掉内部推理标题、System 泄漏残留、冗余 Markdown 围栏，确保输出给客户端的只有纯粹优美的角色台词。

---

## 4. 接口定义 (API Specification)

服务默认监听 `http://127.0.0.1:8876`：

| 方法 | 路径 | 描述 |
|---|---|---|
| `GET` | `/ui` | 打开 Web 版剧本工坊 UI |
| `GET` | `/v1/models` | 返回当前所有可用剧本对应的模型列表 |
| `POST` | `/v1/chat/completions` | OpenAI 兼容聊天补全接口（支持普通 JSON 与 SSE 流式输出） |
| `GET` | `/roleplay/sessions` | 获取所有剧本列表元数据 |
| `POST` | `/roleplay/sessions` | 创建新剧本 |
| `GET` | `/roleplay/sessions/<slug>/<sid>` | 读取指定剧本设定与摘要 |
| `POST` | `/roleplay/sessions/<slug>/<sid>` | 更新指定剧本设定与状态 |
| `DELETE` | `/roleplay/sessions/<slug>/<sid>` | 删除指定剧本所有物理文件 |
| `POST` | `/roleplay/sessions/<slug>/<sid>/archive_mempalace` | **一键归档当前剧本至 MemPalace 专属翼** |

---

## 5. 前端 UI 架构与交互说明

前端界面位于 `plugins/roleplay/ui.html`，设计风格采用**莫兰迪晨曦天青风**（玻璃拟态 Card、浅灰蓝渐变、圆角阴影）：
- **侧边栏 (Sidebar)**：实时拉取本地剧本，支持状态筛选（活跃/暂停）与一键新建草稿。
- **顶部配置卡片 (Config Card)**：
  - 展示标准的第三方接入地址 `http://127.0.0.1:8876/v1`（带一键复制）。
  - 展示当前选中剧本的 Model ID（例如 `roleplay/star_court/save01`，带一键复制）。
- **主体表单 (Main Studio)**：
  - 核心属性：剧本标识 (slug)、存档标识 (session_id)、剧本标题。
  - 世界观与场景：填写背景、地理、时代规则。
  - 沈星回戏里人设：输入特定剧本下的身份性格设定。
  - 言言角色设定：输入言言在剧本中的身份。
  - 开场对白引导：开场情景设定。
- **快捷动作组 (Action Bar)**：
  - 💾 **保存剧本设定**：持久化到文件系统。
  - 🏛️ **归档到 MemPalace**：调用后端写入记忆宫殿并同步知识图谱。
  - ⏸️ **暂停 / 激活**：修改剧本活跃状态。
  - 🗑️ **删除剧本**：二次确认后彻底清理目录。

---

## 6. 运行与调试指南

### 本地启动独立服务
```bash
# 进入项目根目录
cd "/Users/wuchong/Library/Application Support/GenericAgent/runtime/app"

# 启动服务（指定端口 8876）
python frontends/roleplay_api.py --port 8876
```

### 验证服务健康状态
```bash
# 1. 检查模型列表
curl -s http://127.0.0.1:8876/v1/models | jq .

# 2. 模拟发起一轮对话测试
curl -s -X POST http://127.0.0.1:8876/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "roleplay/star_court/save01",
    "messages": [
      {"role": "user", "content": "沈星回，今晚星台的风好像有点冷。"}
    ]
  }' | jq .
```

---

## 7. 后续规划与扩展建议

1. **多分支存档机制**：
   - 目前单剧本采用单个 `session_id`。未来可在 UI 增加“分支另存为”（Branching Save），允许从某一特定轮次克隆新分支。
2. **轻量模型异步摘要**：
   - 当轮次极多时，可让 `update_rolling_summary` 调用轻量级快速模型进行剧情蒸馏，使大纲更加生动细腻。
3. **角色服装/立绘提示卡绑定**：
   - 可以在剧本配置中增加视觉/生图 Prompt 预设，便于言言在对戏时一键生成对应剧本情境下的插图。
