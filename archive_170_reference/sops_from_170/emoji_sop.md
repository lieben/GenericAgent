# emoji_sop（发表情包）

## Clawbot 当前表情协议（已验证）
- 唯一主协议：`[[EMOJI:category]]`。不要改回文件名、短词、`[[EMOJI:category|keyword]]`、`/emoji ...`、`[FILE:path]` 等旧写法。
- 可见回复可以在自然、不过度刷屏时使用表情；推荐把表情 token 作为独立气泡输出，即整条气泡只含 `[[EMOJI:category]]`。
- 内联混排如 `文字[[EMOJI:actingcute]]文字` 由发送端拆成 text / emoji / text 多段；不要为了修表情改发送协议。
- 可用分类来自 `data/agents/<agent>/emojis/<category>/`；发送前由 `_parse_emoji_token()` / `_select_emoji_path()` 随机选图。

## 发送链路红线
- 表情任务必须先读本 SOP 与 `clawbot_group_sop_guard_sop`；不得凭印象摸索。
- 当 `[[EMOJI:category]]` 已经选出 `fpath` 后，图片发送点应调用底层 `orig_send_image(...)`；不要改成会二次拦截/随机化的包装发送。
- 清洗链路必须保留合法 `[[EMOJI:category]]`；不得把 token 当系统说明、日志或文件路径泄漏清掉。
- 禁止修改微信通讯管线、`send_text`、`send_image`、emoji 协议、final-answer 清洗来“试试看”；要先定位证据。

## 修改前验证
- 修改 `plugins/local_frontends/wechat_media_batch.py` 或相关表情逻辑前，至少覆盖：
  1. `[[EMOJI:actingcute]]` 可解析为纯表情；
  2. `私聊的时候[[EMOJI:actingcute]]\n怎么样？` 可拆成 text / emoji / text；
  3. 工具/summary/系统泄漏清洗后不会吃掉合法表情 token；
  4. 已选出 `fpath` 的 emoji 发送点仍是底层图片发送，不发生二次改写。
