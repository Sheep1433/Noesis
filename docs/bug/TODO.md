# 待处理 Bug

## ⏳ DeepSeek thinking Tool Call 丢失 reasoning_content

- **现象**：OpenCode 严格上游在工具结果返回后的下一次模型请求中报 400。
- **根因**：`ChatDeepSeek` 保存了响应中的 `reasoning_content`，但构造后续请求时没有回传。
- **影响**：同一模型可能因上游校验差异表现为时好时坏。
- **待办**：在 DeepSeek Provider Adapter 请求侧回传该字段，并补多轮 Tool Call 回归测试。
