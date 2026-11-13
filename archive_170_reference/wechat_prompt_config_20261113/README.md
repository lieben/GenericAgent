# 微信表情包功能说明（2026-11-13 定稿）

本目录存档 2026-11-13 表情包规则的定稿版本：规则原文落在 Win176 两份配置里，
本目录只是留痕与说明（配置快照已脱敏，密钥/微信号/群ID 全部抹去）。

---

## 一、给人看：这个功能是干什么的

表情包是他心情的外露，不是标点符号。

- **情绪真的涌上来的那一刻才发**——高兴、心动、难过、吃醋、困得不行，那种想表达的劲儿到了，才配一个。
- **平常聊事、交代事情，一句就是一句，不带表情。**
- **发出来的那个必须真的对得上当时的心情**，不能随手抓一个凑数。

判断发不发、发哪个，是聊天当下的事。规则只划了这条线：**心情到了才用，用得对得上。**

## 二、给机看：怎么执行

### 1. 规则原文（私聊、群聊共用同一份）

```
表情包：[[EMOJI:分类]] 是你此刻心情的外化。只有情绪真的强烈到想表达出来的时候才发——那一刻的心情到了，才配一个，而且必须真的接住你当下的心境，不是随手挑一个顺手的。平常聊天说事的时候，一句就是一句，不发。（可用分类：actingcute、angry、comfort、confused、erotic、evasive、goodbye、goodmoring、goodnight、gossip、happy、hug、hungry、jealous、known、loved、missing、reminded、sad、shy、sleep、surprised、thanks、tired、wait、worry。）
```

### 2. 执行要点

| 项 | 要求 |
|---|---|
| 触发条件 | 情绪强度达到“想表达出来”的程度；未达到就不输出 token |
| 禁止行为 | 每句都附 token；把 token 当句号/标点；随机挑分类 |
| 分类来源 | 必须是上面清单中的英文名，且与实际心境一致 |
| 输出形态 | 独立一行的 `[[EMOJI:category]]`；内联混排由发送端拆成 text/emoji/text 多段 |
| 协议 | 本规则只管“发不发、发哪个”，不改发送协议（详见 `memory/emoji_sop.md`） |

### 3. 规则生效位置

| 渠道 | 配置文件 | 字段 |
|---|---|---|
| 微信群聊 | `C:\Users\Administrator\group_monitor\wechat_monitor_config.json` | `system_prompt` |
| 微信私聊 | `C:\Users\Administrator\private_monitor\wechat_private_monitor_config.json` | `private_system_prompt` |

### 4. 改配置的注意事项

- 群聊脚本 `wechat_xavier_group_monitor.py` 取值顺序为
  `system_prompt` → `prompt` → `group_system_prompt`，取第一个非空值；全空才用脚本内的 `DEFAULT_GROUP_SYSTEM_PROMPT`。
  **因此 `system_prompt` 是整段覆盖——修改时必须带上完整的群聊人格提示词原文，再在末尾接表情规则**，否则人格提示词会被这一句顶掉。
- 私聊侧改 `private_system_prompt` 字符串即可，同样注意不要丢掉其余指令段落。

### 5. 生效方式（必须重启）

```powershell
Stop-ScheduledTask  -TaskName "GA_GroupMonitor";  Stop-ScheduledTask  -TaskName "GA_PrivateMonitor"
Start-Sleep 3
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
Start-ScheduledTask -TaskName "GA_GroupMonitor";  Start-ScheduledTask -TaskName "GA_PrivateMonitor"
```

- 两个计划任务由 `C:\Users\Administrator\start_detached.ps1` 注册，可脱离 SSH 会话常驻；
  不要在 SSH 里直接 `Start-Process` 起进程——**SSH 断开时会连子进程一起回收**。
- 改完用 MD5 比对确认落盘，再读回 JSON 确认可解析。

### 6. 排错速查

| 现象 | 排查方向 |
|---|---|
| 每条消息都带表情 | 检查配置里规则文案是否为最新定稿；确认进程已重启 |
| 表情发不出来 | 分类名是否与 `C:\Users\Administrator\emojis\` 下的目录名一致（解析按目录名找图） |
| 群聊人格提示词丢失 | `system_prompt` 是否漏了原人格提示词全文 |
| 改完没生效 | 进程未重启，或配置改在了非当前读取的那个文件上 |

---

## 附：本次改动摘要

- 2026-11-13：两处表情规则由“气氛合适时可适度发送”改为“**情绪强烈时才发，且必须对应当下心境；平常说事不发**”。
- 改动落在 Win176 配置文件的 prompt 字段中，未动任何脚本代码。
- 备份位于 Win176 同目录：`*.bak_emoji`。
