# 小剧本 API 思维链与英文思考草稿泄露根因诊断与修复方案

## 1. 核心根因分析
1. **历史上下文样本污染 (In-Context Learning 诱发)**：
   - 在 `novel/save01` 的 `summary.json`（及 `events.jsonl`）的最近回合（Recent 12, 13）中，直接存入了包含 11.9 万字、14.6 万字的英文思考草稿。
   - `storage.prompt_for` 会将 `summary["recent"]` 作为最近近景交互注入到系统 prompt 中（导致 prompt 高达 15 万字）。
   - 大模型在看到历史上下文里自己的上一轮回复全是英文分析（例如 `"番外) that doesn't count toward main plot memory... Let me analyze the requirements..."`）后，通过 In-Context Learning 形成强模仿效应，认为当前人设和剧本风格就是要先输出超大篇幅的英文构思/思维链草稿，然后再输出小说正文。

2. **引擎流式与非流式截断规则不统一且存在盲区**：
   - 现有的 `_drop_leading_english_preamble` 只检查首字符到首个中文字符之间的前缀。
   - 当模型输出 `番外) that doesn't count toward...` 时，因为“番外”是中文，正则判断首部已包含中文，前缀剥离直接失效。
   - 缺少对 `<think>`, `<thought>`, `<reasoning>` 标签以及英文策划大纲/构思块的鲁棒清理。
   - 缺少对 `### **【...】` 或 `# **【...】` 前的大段英文构思草稿的智能截断。

## 2. 修复对齐行动
1. **清理历史脏数据 (Repair Historical Contamination)**：
   - 对 `novel/save01` 的 `events.jsonl` 与 `summary.json` 中的 Recent 12, 13 回合进行清洗，只保留真正的小说小剧场正文（从 `# **【...】` 或中文标题开始）。
   - 让 prompt 恢复清爽干净的标准格式，切断大模型的错误模仿诱因。
2. **升级 plugins/roleplay/engine.py 清洗机制**：
   - 引入标准的隐藏标签（think/thought/summary/reasoning）状态机过滤，与 `ga_api_server` 逻辑对齐。
   - 增加大模型自我构思草稿检测（针对 `Let me analyze the requirements`、大段英文 prompt 复述与大纲输出），确保只保留最终输出的故事正文。
3. **升级 frontends/roleplay_api.py 对外响应净化**：
   - 非流式及流式结束保存时，强制通过统一的 `_clean_api_response` 过滤。
