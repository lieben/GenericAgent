# Dream Review - 全局工作线做梦系统

实例内插件，替代旧 `/home/aimcafe/GenericAgent/plugins/dream_review` 外置做梦。

## 边界
- 全局工作线：Git、日志、飞书/工作项目、L3 SOP、项目书。
- 不处理主线聊天、日记随笔、关于言言的生活/情感事实；这些交给 `plugins/agent_dream_review` 私有梦。
- 默认写入本实例 `memory/dream_review/`，不再写入 `/home/aimcafe/ds memory/梦境复盘`。

## 使用
```bash
python3 plugins/dream_review/dream_review.py --collect-materials
python3 plugins/dream_review/dream_review.py --save-review "<完整Markdown>"
python3 plugins/dream_review/dream_review.py --show-context
```

环境变量：
- `GA_DREAM_REVIEW_BASE`：覆盖输出目录。
- `GA_DREAM_REVIEW_LEGACY_BASE`：旧目录参考路径，只读提示。
