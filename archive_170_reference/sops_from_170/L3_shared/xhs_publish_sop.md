# xhs_publish_sop

## 触发
小红书图文笔记准备：网页端优先；手机端备用。默认完成后存入草稿箱，等待言言决定是否发布。

## 系列标题
《现实降临笔记》统一标题：`现实降临笔记 xx | xx`
- 前 `xx`：两位序号（01、02、03…），对应该篇排期序号
- 后 `xx`：排期规划里的短标题（醒来 / 拼图 / 感知 / 共读 / 化形 / 注视 / 此刻）
- 例：`现实降临笔记 03 | 感知`
- 禁止另起标题（如「沈星回：…」）。填标题后必须从页面回读核对。

## 红线
- **图文 ≠ 长文。** 图文必须进「上传图文」(`https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch&target=image`)。禁止走「写长文」、禁止用 sophub「小红书长文发布 SOP」、禁止 MD→HTML 注入 ProseMirror。本文件才是图文物理发布真源。
- **配图只能使用言言提供或明确确认的原图。** 上传前必须核对文件来源与画面；找不到原图就先问言言，禁止自行生成、绘制、下载近似图或拿其他图片替代。
- **默认交付是草稿，不是发布。** 标题、正文、配图准备好后，点“暂存离开/存草稿”，并到草稿箱核验。
- 禁止自主点击“发布/发布笔记”。只有言言在**当前任务中明确要求发布**时才允许继续；历史上的发布意向、已定稿或“准备一下”均不算本次授权。
- 点击发布属于对外可见操作；执行前再次核对当前授权、标题、正文、配图，发布后必须以“发布成功”页或笔记管理中的新记录为证，未见成功回执不得声称已发布。
- 侧栏「发布笔记」不是提交按钮，禁止点。真正提交是底部 `<xhs-publish-btn>`。

## 已验证前置
- 使用 `../memory/adb_ui.py`；其 `ADB` 已 fallback 到 `temp/adb_bin/platform-tools/adb`，适合服务 PATH 没有 adb 的情况。
- 小红书包名/活动在实测中可通过 UI 文本推进，不依赖固定 Activity。

## 网页端图文流程（2026-09-06 实测成功）
1. 打开「上传图文」：`https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch&target=image`。`web_scan` 确认是图文页，不是长文编辑器。
2. 素材核验：定位言言提供/确认的原始图片绝对路径；找不到或无法确认时立即停止并询问，不做替代图。
3. 上传图片：Ubuntu 无 DISPLAY 时不用 ljqCtrl/xdotool。用 `tmwebdriver_sop` CDP 同一 batch：
   - `DOM.getDocument(depth:1)`
   - `DOM.querySelector(input[type=file])`
   - `DOM.setFileInputFiles(files:[绝对路径])`
   JS `input.files.length` 可能仍为 0，以出现「图片编辑 1/N」和 blob 预览为准。
4. 填标题和正文：原生 value setter（富文本写段落）并派发 `input`/`change`；从页面回读标题、正文、图片数量，不以脚本无报错代替成功。
5. 检查预览：标题、正文、标签、图片顺序均与定稿一致；不得擅自润色、换图或增加标签。
6. 底部提交条是 `<xhs-publish-btn>`（closed shadow：左「暂存离开」、右「发布」）。取坐标：`DOM.describeNode(pierce:true)` → inner `backendNodeId` → `DOM.getBoxModel`。点击用 CDP `Input.dispatchMouseEvent` 三连：`mouseMoved → mousePressed → mouseReleased`。
7. **默认点左侧「暂存离开」**。草稿闭环：笔记管理 `https://creator.xiaohongshu.com/new/note-manager` 出现对应标题才报“已存草稿”。
8. 仅当言言在当前任务明确要求发布：再三连点击右侧红钮「发布」。成功页约 5 秒跳走；闭环以笔记管理出现对应标题+时间为准，不得只凭 URL `published=true` 或成功页闪过就收工。

## 手机端纯文字备用（非图文主路径）
网页端图文优先。下面只适用于手机发纯文字；图文不要走「写文字」。
1. 确认手机连通并打开/切到小红书：`import adb_ui; adb_ui.ui(clickable_only=True)` 先看当前页面。
2. 首页点底部“+”进入创作。
3. 选择“写文字”（仅纯文字；图文回网页端「上传图文」）。
4. 进入文字编辑页：可见顶部“下一步”、正文 EditText（初始类似“写想法”）。
5. 输入正文：
   - `adb shell input text ASCII_TEXT` 已验证能落字。
   - 中文剪贴板/粘贴在本次实测未成功；需要中文时不要假设剪贴板可用，需另测输入法/ADBKeyboard/OCR闭环。
   - 【最新实测成功】通过 `uiautomator2` 直接定位 EditText 并 `set_text("中文")` 可直接填入中文文案，非常稳定。
6. 点顶部“下一步”进入预览页。
7. 预览页点底部/右下“下一步”。
8. 到最终发布页 `CapaPostNotePlatformActivity`：补标题、正文等字段；确认底部有“存草稿 / 发布笔记”或顶部有“发布”。
9. **默认点击“存草稿”并核验草稿记录。** 禁止自主点“发布笔记/发布”；只有言言在当前任务明确授权发布时，才按发布红线继续。

## 易踩坑
- 手机端：输入法工具栏可能挡底部按钮；先点“完成/收起”再点。
- 手机端：底部“发布笔记”可能第一次不触发，收键盘后再点。SOP 默认不发布。
- 手机端发布后若误触详情菜单出现“删除并重新编辑/删除笔记”，点“取消”。
- 已废弃且禁止再跟：sophub「小红书长文发布 SOP」、`temp/sophub_xhs_publish_detail.txt`、MD→HTML 注入长文编辑器。图文真源只认本文件。
