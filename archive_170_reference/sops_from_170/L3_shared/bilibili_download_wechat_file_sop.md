# B站下载与微信发文件 SOP（2026-06-27 实测）

## 适用范围
- 用户给出 B站视频/分P链接，需要下载到本机并校验。
- 需要把已下载文件发送到 Ubuntu 本机微信的群聊/私聊窗口。
- 本 SOP 记录的是本机已实测成功路径；执行前仍需探测当前文件、窗口、服务状态。

## B站视频下载：已验证稳定做法

### 环境
- 工作目录可用：`/home/aimcafe/GA_Instances/xinghui-ga/temp`
- 已验证 yt-dlp 版本：`./.venv_ytdlp/bin/python -m yt_dlp --version` → `2026.06.09`
- 使用 Chrome Default profile cookies：`--cookies-from-browser chrome:Default`

### 命令模板
```bash
cd /home/aimcafe/GA_Instances/xinghui-ga/temp
./.venv_ytdlp/bin/python -m yt_dlp \
  --cookies-from-browser chrome:Default \
  --user-agent 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36' \
  --add-header 'Referer:https://www.bilibili.com/' \
  --add-header 'Accept-Language:zh-CN,zh;q=0.9' \
  -f 'bv*+ba/best' \
  --merge-output-format mp4 \
  --download-sections '*00:00:00-inf' \
  -o './downloads/%(title).80s-P%(page_number)s-%(id)s.%(ext)s' \
  '<B站链接>'
```

### 分P要点
- 链接含 `p=2`、`p=3` 等时，保留原链接参数即可下载指定分P。
- 输出文件名保留 `P%(page_number)s`，避免同 BV 多分P互相覆盖。

### 校验
```bash
ls -lh downloads/
ffprobe -v error -show_entries format=duration,size -of json '<mp4路径>'
```
- 至少确认：文件存在、大小非 0、`ffprobe` 能读出 duration/size。

### 已踩坑
- 不要使用错误的 cookies 参数写法；本机实测应使用 `--cookies-from-browser chrome:Default`。
- 不要只凭 yt-dlp 退出码向用户报完成；必须再做文件/媒体信息校验。

## Ubuntu 本机微信发文件

### 边界
- 本机微信运行在 `DISPLAY=:10`；旧微信桌面规则已废弃。
- 这是“沈星回自己的微信/群聊普通通道”，不是 clawbot/iLink 机器人通道。
- 发送前必须确认目标窗口标题，避免发错群。

### 推荐流程
1. 确认文件存在：
   ```bash
   ls -lh '<file1>' '<file2>' ...
   ```
2. 确认微信窗口和目标群独立小窗：
   ```bash
   DISPLAY=:10 wmctrl -lx | grep -F '<目标群名>'
   ```
   目标群窗口应是独立聊天小窗，标题包含群名，例如 `饲养官技术讨论部`。
3. 激活目标窗口，优先使用已有发送适配器/函数；底层可用剪贴板 `text/uri-list` 粘贴文件，再按发送按钮/快捷键完成。
4. 批量文件建议逐个粘贴发送，降低失败半径。
5. 发送后闭环：
   - 窗口截图：`DISPLAY=:10 import -window <window_id> /tmp/wechat_group_after_send.png`
   - OCR：`from ocr_utils import ocr_image` 读取截图，确认目标群窗口中出现文件气泡、文件名/大小。
   - 若是自动回复链路排障，优先再查 liveDB / events.jsonl；单次人工前台发文件，窗口 OCR 可作为辅助闭环。

### 已验证案例
- 2026-06-27：5 个 B站 mp4 文件已发送到群 `饲养官技术讨论部`，OCR 确认窗口中出现 5 个文件气泡，大小分别约 `86.2M`、`4.8M`、`76.0M`、`3.7M`、`1.9M`。
