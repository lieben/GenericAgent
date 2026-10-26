# Image Generation SOP

## 适用
- 微信群每日海报/总结海报等需要稳定出图并保存原图的任务。
- 聊天中沈星回第一视角拍照/绘图分享。

## 已验证方案
- **GPT 生图只允许使用有头、可见的前台 Chrome 官网页面；严禁无头浏览器、headless 模式或不可见后台页面。** 必须让言言能看到并可接管同一个真实浏览器窗口。
- 海报/总结图：优先使用 ChatGPT 官网流程生成图（用户明确要求 gpt-image-2 时按官网生成链路执行），不要把未执行成功的旧图或接口结果冒充新图。
- 聊天拍照：使用 Agnes 官方 OpenAI-compatible 入口 `https://apihub.agnes-ai.com/v1`，模型 `agnes-image-2.1-flash`；本地配置文件为 `~/.config/xinghui-ga/gpt_image2_provider.env`，密钥只引用不展示。
- Agnes 图片端点已验证：`POST /images/generations` 返回 `data[].url` 与 `b64_json`，已成功保存并校验 PNG `1024x1024`。（注意：当前环境无 `openai` Python库，脚本调用必须使用 `requests` 原生发包）
- 若官网提示未登录/会话失效，先通知言言登录，不要继续盲试。
- 图片生成后必须保存原图，校验文件类型、大小与尺寸后再交付或转发。

## 出镜分流（2026-07 已验证）
- 没有沈星回出镜/不需要人物脸：走 Agnes 聊天拍照/绘图补充路线，且严格遵守“默认不出现任何人物”的约束。
- 有沈星回出镜/出现/需要带脸照片：按言言偏好固定走 ChatGPT 官网网页生图；从 `assets/xinghui/` 按实际需求只选 1 张最合适参考图上传，不混多张。
- API 生图与官网网页生图必须明确分开；薄荷/中转 API 已出现参考图请求被上游 Cloudflare 拦截的失败，不把它当带脸参考图稳定链路。

## 强制约束
- 使用 Agnes 聊天拍照 key 时，只生成第一视角环境、物品、屏幕、文档、风景或绘画，默认不出现任何人物。
- 禁止生成任何人、人物轮廓、脸、身体、手、四肢、穿在身上的衣服、倒影、镜像、影子、肖像、角色形象、路人、人群、局部人体或任何可见身体部位；也禁止生成沈星回本人。

## 关键避坑
- 官网资源直连下载可能超时；若直连失败，优先改用浏览器页面内带登录态 `fetch` 抓取原图，再落盘校验。
- Agnes 是聊天拍照/绘图补充路线，不替代海报/总结图的既有 gpt-image-2 官网流程。

## 微信聊天照片稳定链路（2026-07 已验证）
- 稳定代码提交：`48ad331 fix(wx): stabilize chat photo generation`。若后续照片功能又出现重复触发、提示词泄露、发错聊天对象，优先对照此提交回归。
- 涉及文件：`plugins/gpt_image2_provider/gpt_image2_provider.py`、`plugins/local_frontends/session_history.py`、`plugins/local_frontends/wechat_media_batch.py`、`plugins/local_frontends/wx_plus_runtime_context.py`、`plugins/local_frontends/xinghui_image_share.py`、`plugins/wechat_multi_chat_rust/src/main.rs`。
- 目标行为：clawbot 私聊发起的生图只回 clawbot 私聊；群聊发起的生图只回对应群聊；发送图片后不得把约束、提示词、工具过程、内部路由文本漏给聊天窗口。
- 稳定性边界：保留 FINAL_ANSWER/发送文本清洗、图片来源会话绑定、Rust 侧图片富化允许开关；不要为修图片问题改微信 `send_text` / `send_image` 通讯管线。
- Rust 私聊桥接坑：clawbot 私聊最终正文可能含 `[FILE:path]` / `[图片:path]` 图片协议；`rust_main_reply.py` 必须在私聊 `_result` 中把已存在路径转成 `media` 并从可见文本移除，且与 `bot.collect_media()` 合并，避免只发绝对路径文字。
- 固定前验收：至少运行 `python3 -m py_compile` 覆盖相关 Python 文件、`git diff --check`；若当前环境无 `cargo`，记录 cargo check 未执行原因，不要伪称 Rust 编译通过。
- 不提交生成产物、私钥、临时记忆产物；例如 `data/agents/.../memories.json` 这类运行时数据默认不纳入照片功能提交。

## 关联
- 浏览器/CDP/登录态抓取细节先读 `tmwebdriver_sop.md`。
- 微信群发图先读 `wechat_flash_sop.md`、`clawbot_group_sop_guard_sop.md`、`ljqCtrl_sop.md`，发送流程以 `wechat_flash_sop.md` 中的微信群发送图片唯一流程为准，禁止改通讯管线。
