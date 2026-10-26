# Custom Plugins Package - Distribution Checklist

## 包含文件（需要分发）

### 1. 核心插件目录
```
plugins/local_frontends/
├── prompt_config.py              # 配置加载器（新增）
├── plugin_prompts.example.json   # 配置模板（新增）
├── session_history.py            # 已配置化
├── export_group_runtime_context.py  # 已配置化
├── xinghui_image_share.py        # 已配置化（{agent}/{user}占位符）
├── wx_group_shadow_reader.py     # 已配置化
├── wx_plus_proactive.py          # clawbot主动发起
├── wechat_media_batch.py         # clawbot媒体批次入口
└── ... (其他已有文件)

plugins/roleplay/                # 独立小剧场前端与存储逻辑
plugins/roleplay_bridge.py       # 主线/API/clawbot 只读阅读小剧场的动态工具插件
frontends/roleplay_api.py        # 独立小剧场 HTTP/OpenAI 兼容入口
```

### 2. Rust多会话插件目录
```
plugins/wechat_multi_chat_rust/
├── adapters/
│   ├── rust_llm_generate.py      # 已配置化
│   ├── rust_main_reply.py        # 已配置化
│   └── ... (其他adapter文件)
└── ... (Rust子仓所有文件)
```

### 3. 依赖的共享工具（如果分发端没有GA原生）
```
# 如果对方GA实例缺失这些，需要一并提供：
- vision_api.py (识图)
- ocr_utils.py (OCR)
- video_utils.py (视频)
```

---

## 排除项（不要打包）

### 1. GA原生代码
- `agentmain.py`, `agent_loop.py`, `ga.py`, `llmcore.py`
- `assets/sys_prompt.txt`
- `frontends/*app.py`（除非你修改过）

### 2. 个人记忆/配置
- `memory/` 整个目录（L1-L4）
- `temp/` 整个目录（包括 wechat_state 等运行时状态）
- `config/frontend_mode_config.json`（个人人设配置）
- `data/agents/xinghui/emojis/`（个人表情包库）
- `plugins/local_frontends/plugin_prompts.json`（你的实际配置，只给 example）

### 3. 运行时缓存
- `plugins/local_frontends/runtime/`
- `*.pyc`, `__pycache__/`
- `.git/`（如果打包子仓，可以保留 .git 或用 zip）

---

## 使用说明（给接收方）

### 1. 安装前置条件
- 已安装 GA 官方最新版（包含原生 memory/session/llmcore）
- Python 环境已配置
- 如果使用微信功能，需本机有 xwechat 或对应微信环境

### 2. 安装插件
```bash
# 1. 解压插件包到 GA 根目录
cd /path/to/your/GA_Instances/your-agent-ga/
unzip custom_plugins.zip

# 2. 复制配置模板并填写你的人设
cp plugins/local_frontends/plugin_prompts.example.json \
   plugins/local_frontends/plugin_prompts.json

# 编辑 plugin_prompts.json，填入你的：
# - agent_name: 你的角色名
# - user_name: 你的用户名
# - 其他 prompt 模板（如需自定义）
```

### 3. 配置示例
```json
{
  "common": {
    "agent_name": "你的角色名",
    "agent_name_en": "YourAgentName",
    "user_name": "你的用户名",
    "user_name_en": "YourUserName",
    "reply_prefixes_to_strip": ["回复：", "回复:", "正文：", "正文:"]
  },
  "session_history": {
    "trailing_process_note_sender_pattern": "(?:用户|对方)"
  },
  "export_group_runtime_context": {
    "timeline_intro": "以下是今天{agent_name}视角下的群聊话题时间线：",
    "recent_dialog_intro": "以下是最近30分钟{agent_name}与{user_name}的对白原文（不含群聊）：",
    "no_recent_dialog": "（无最近对白）"
  },
  "rust_main_reply": {
    "generation_wrapper_markers": [
      "[微信多聊Rust系统",
      "你正在以同一个主会话参与多个微信目标",
      "[本轮目标原始规则]",
      "[本轮现场消息]",
      "请严格遵守\"本轮目标原始规则\"和群聊 DECISION 协议",
      "请你作为角色本人，结合以上",
      "最新消息："
    ]
  }
}
```

### 4. 验证配置
```bash
# 编译检查所有插件语法
python3 -m py_compile plugins/local_frontends/*.py
python3 -m py_compile plugins/wechat_multi_chat_rust/adapters/*.py

# 扫描残留硬编码（应该返回 0）
grep -rn "沈星回\|言言\|Xavier\|YanYan" \
  plugins/local_frontends/*.py \
  plugins/wechat_multi_chat_rust/adapters/*.py | wc -l
```

### 5. 启动服务（以 clawbot 为例）
```bash
# 如果你使用 systemd 服务
systemctl --user start xinghui-ga-wechat.service
systemctl --user status xinghui-ga-wechat.service

# 查看日志
journalctl --user -u xinghui-ga-wechat.service -f
```

---

## 配置化完成度

✅ 已配置化的模块：
- `prompt_config.py`: 统一配置加载器
- `session_history.py`: 尾注发送者正则
- `export_group_runtime_context.py`: 时间线/对白引言模板
- `xinghui_image_share.py`: 文档/注释中的角色名用 `{agent}/{user}` 占位符
- `wx_group_shadow_reader.py`: 注释通用化
- `rust_llm_generate.py`: 回复前缀清洗列表
- `rust_main_reply.py`: 包装器标记元组

✅ 人设硬编码残留：**0** 处（已验证）

---

## 注意事项

1. **配置文件必填**：插件依赖 `plugin_prompts.json`，启动前必须从 example 复制并填写。
2. **不要修改 GA 核心**：本插件包只覆盖 `plugins/` 和 `frontends/` 非核心文件，不影响 GA 原生 memory/session 类。
3. **子仓独立管理**：`plugins/wechat_multi_chat_rust/` 和 `plugins/local_frontends/` 是独立 git 子仓，可单独版本管理。
4. **升级兼容性**：GA 官方升级时，只需保持插件目录不被覆盖，或重新应用插件包。

---

## 支持的功能

- ✅ Rust 多会话微信监控（群聊+私聊）
- ✅ Clawbot 机器人通道（iLink Bot API）
- ✅ 媒体批次识图（图片/视频/OCR）
- ✅ 群聊时间线感知 + 主动发起
- ✅ 私聊上下文净化 + 历史持久化
- ✅ 表情包随机发送（按分类）
- ✅ 配置化 prompt 模板（无硬编码人设）

---

生成时间：2026-06-12
