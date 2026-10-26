# 旧记忆退役每日评测 2026-08-19

## 结论
- 总状态：BLOCKED
- 连续 PASS：0 天
- 是否满足退役条件：否（旧L2读取和旧Hook仍在，尚未完成所有受控测试）

## 原生 L1–L4
- global_mem_insight：存在 / 9f6f1ffaa3e821adc44450971f36e3f1d4cf498f7cacf2c59ed638f87860c86d / 30行
- global_mem：存在 / 343c3aecb90df3f6de4156128e949270ba192bf52ff7828c8dcf6c4208ef643b / 42行
- L3/L4：可读（L3 共 85 个文件，L4 有 117 个会话）
- 结论：原生记忆链完整未被破坏。

## 旧链残留
- 旧 Hook：`plugins/life_memory_plugin.py` 仍可被导入（存在），且在多个微信/多聊前端配置中被引用。
- L2 读路径：`frontends/openai_api.py`、`frontends/api_memory_recall.py` 等文件中存在大量 `L2_facts.txt` 读取路径。
- L2/本地库写路径：`plugins/agent_dream_review/agent_dream_review.py` 中仍然存在自动写入 `L2_facts.txt` 的逻辑（自动提取新事实并写入）。
- 与昨日相比：首日执行，无对比。

## Memory Palace
- boot：因未配置 MCP 命令行客户端工具路径，本次未能完成只读调用。
- 工具/索引：未测。
- scope：未测。
- search→read：未测。
- 五类抽检：未测。

## 梦链与多入口
- 梦配置/门控/Guard：未找到统一 `config.json` 中的 `auto_write_l2_from_dream` 字段；但在 `adapt_dream_to_memory_palace.py` 中发现 Guard=ADD 逻辑，说明新链也处于开发/测试态。
- API：未测。
- 微信/Clawbot：未测。

## 门槛计分
| 项目 | 结果 | 证据 |
|---|---|---|
| 原生 L1/L2 未变化 | PASS | 哈希值与文件均在 |
| 旧链禁用 | FAIL | L2 依然在多处前端、API和梦链被调用/写入 |
| Memory Palace 工具 | ERROR | 官方工具链未连通，无法执行只读抽检 |

## 阻塞项与建议
- **旧链强依赖**：当前 `agent_dream_review` 和多个前端仍依赖旧的 `L2_facts.txt`。需要完成代码替换才能达到退役要求。
- **评测工具受限**：本次无法在 bash 环境中通过 CLI 连通并只读抽测 Memory Palace 事实。后续建议明确提供供评测脚本直接调用的 MCP Read 工具。
