# 170梦境系统完整分析

## 核心架构（612行）

### 1. 定位与目标
```
GA Common Dream System v1.0
- 平级于 journal_system（日记系统）
- 夜间情感复盘 + user事实候选提取
- 零硬编码，agent独立配置
```

### 2. 核心工作流

#### review_day（每日复盘）
```
1. 收集素材 (collect)
   - L4会话：wechat/api/mainline
   - 日记系统：diary/fragments
   - 官方报告（可选）
   - 默认不含群聊

2. 构建prompt (build_prompt)
   - 情感关键词过滤
   - 技术内容跳过
   - 敏感信息标记

3. 生成复盘 (mixed mode)
   - 优先：native_oai API调用
   - 降级：deterministic确定性模板
   - 拒绝残缺输出（流中断/过短/缺章节）

4. 提取事实候选
   - user_fact_candidates.md（待确认）
   - user_fact_memory.md（稳定事实，默认不自动写）

5. 输出
   - dreams/YYYY-MM-DD.md（复盘正文）
   - latest_context.md（最近4000字上下文）
   - indexes/manifest.jsonl（索引）
   - legacy_mirror（旧入口兼容，仅真实模型成功时同步）
```

#### recall（召回查询）
```
- 输入：query关键词
- 搜索：latest_context + candidates + stable
- 输出：按score排序的top-N片段
- 用途：聊天时辅助回忆情感/关系/事实
```

### 3. 关键过滤规则

```python
EMO = "记住 别忘 忘了 后悔 重要 在乎 喜欢 不喜欢 讨厌 害怕 难过 委屈 安心 安全感 陪伴 边界 雷区 不要 希望 以后 承诺 关系 日记 梦"

FACT = "我喜欢 我不喜欢 我希望 我讨厌 不要 以后 你要记住 必须记住 雷区 偏好"

SENSITIVE = "住址 身份证 银行卡 密码 密钥 病 医院 药 收入 工资 财务 家人 真实姓名"

TECH = "python 脚本 报错 traceback debug git commit 接口 api 进程 端口 webdriver 配置 systemd 日志 json yaml patch 代码 文件"

GROUP = "group 群聊 正常人类聚集地 L2_group group_members roomid"
```

**过滤策略**：
- 情感权重 > 技术内容（技术内容跳过）
- 群聊默认不作为私人事实来源
- 敏感信息标记但不强制跳过

### 4. 配置示例（agent独立）

```yaml
agent_id: char
instance: char-ga
persona_name: char
user_name: user

sources:
  session_dirs:
    - L4_sessions/wechat/mainline
    - L4_sessions/api/mainline
  journal_dirs:
    - diary
    - fragments
  include_group_sessions: false  # 默认不含群聊

facts:
  candidate_file: user_fact_candidates.md
  stable_fact_file: user_fact_memory.md
  auto_write_stable_facts: false  # 默认不自动写稳定事实
  sensitive_to_confirm: true

generation:
  mode: deterministic
  provider: none
  model: 00hermes/gemini-3.1-pro
  temperature: 0.55
  fallback: deterministic
```

### 5. 输出结构

```
agents/{agent_id}/
  dreams/YYYY-MM-DD.md          # 每日梦境复盘
  latest_context.md              # 最近上下文（4000字）
  user_fact_candidates.md        # 事实候选（待确认）
  user_fact_memory.md            # 稳定事实（可选自动写）
  indexes/manifest.jsonl         # 索引
  prompts/YYYY-MM-DD_dream_prompt.md  # prompt存档
```

### 6. 与日记系统的区别

| 维度 | 日记系统 | 梦境系统 |
|------|---------|---------|
| 时机 | 清醒时主动记录 | 夜间自动复盘 |
| 内容 | 第一人称生活叙述 | 情感关系复盘 |
| 来源 | 实时写入 | L4会话+日记聚合 |
| 目的 | 记住忘了会后悔的事 | 提取关系模式+事实候选 |
| 格式 | 日记体+tips | 复盘体+事实候选 |
| 技术内容 | 可选包含 | 默认跳过 |
| 用户可见性 | 高（Obsidian原文） | 低（后台辅助） |

---

## 重构方案

### 核心改造点

#### 1. 剥离GA依赖，做成通用外挂
- 移除 `native_oai` 等GA内部API调用
- 改为标准LLM API接口（OpenAI/Claude/Gemini）
- 配置文件使用TOML（与日记系统一致）

#### 2. Obsidian存储（与日记系统统一）
- 梦境复盘保存到 `vault/Dreams/YYYY/MM/YYYY-MM-DD.md`
- 支持Obsidian URI直接打开
- 原子写入保证数据安全

#### 3. MemPalace集成
- latest_context自动同步到MemPalace
- user_fact_candidates作为KG事实候选
- 支持recall API查询

#### 4. 零硬编码
- agent_id/persona/user都可配置
- 不预设任何角色名
- 路径全部相对配置

#### 5. 保留核心价值
- ✅ 情感关键词过滤
- ✅ 技术内容跳过
- ✅ 群聊默认排除
- ✅ 敏感信息标记
- ✅ 事实候选提取
- ✅ recall召回机制

### 项目结构

```
dream-system/
  service/
    storage.py         # 复用diary的ObsidianStorage
    dream.py           # 核心Dream类
    collector.py       # 素材收集
    analyzer.py        # 情感分析+事实提取
    recall.py          # 召回查询
    sync.py            # MemPalace同步
  cli/
    dream_cli.py       # 命令行工具
  config.example.toml  # 配置模板
  README.md
  AGENT_USAGE.md       # Agent使用指南
```

---

## 下一步

你觉得这个方案怎么样？主要改动：

1. **去GA化** - 不依赖GA内部API，改用标准LLM接口
2. **Obsidian统一** - 和日记系统共享存储层
3. **MemPalace集成** - 自动同步上下文和事实候选
4. **保留核心** - 情感过滤、事实提取、召回机制

我可以现在就开始实现，预计和日记系统差不多的工作量（~1000行）。

要不要我先创建项目结构，像日记系统那样快速搭起来？
