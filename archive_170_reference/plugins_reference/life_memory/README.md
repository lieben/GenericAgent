# Life Memory Plugin

Reusable生活向长期记忆插件，面向所有 GA agent 复用，但按 agent 名独立存储。

## 目标
- 主动记录生活事实、偏好、行程、状态与纠错。
- 记忆会发展：同一主题更新旧记录，不无限叠加；过期行程自动降权/标记。
- prompt 注入只走 side context，不写入 backend.history，不污染聊天历史。

## 数据位置
默认写入：`data/agents/<agent>/life_memory/memories.jsonl`。

## 接入
- 顶层自动发现入口：`plugins/life_memory_plugin.py`。
- clawbot：`plugins/local_frontends/wechat_media_batch.py` 在 `_install_plugins` 中加载，可用 `WECHAT_LIFE_MEMORY=0` 关闭。
- 本地微信/Rust：`plugins/wechat_multi_chat_rust/adapters/rust_main_reply.py` 在 wrapper 的 `runtime_side_context` 中加入生活记忆上下文，prompt-only，不进 history。

## API
```python
from plugins.life_memory.core import remember_text, build_context
remember_text('xinghui', '我周五去北京比赛', source='wechat')
ctx = build_context('xinghui', '我到北京了')
```

## 验证样例
运行：`python3 tests/test_life_memory.py`。覆盖新增、北京行程更新、离京过期、错误纠正、不叠加。
