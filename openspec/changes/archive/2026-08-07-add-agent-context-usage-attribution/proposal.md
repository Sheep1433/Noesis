## Why

Noesis 当前只能展示最新模型请求的上下文总量估算，以及一轮 Agent 多次模型调用累计的 input/output token。用户无法判断上下文由哪些内容占用，也无法判断消耗发生在主 Agent、子 Agent还是中间件调用中，因而难以定位 prompt、Skills、工具结果或执行路线造成的上下文膨胀与成本异常。

本变更参考 DeerFlow 的 usage attribution，但保留 Noesis 在最终 `ModelRequest` 上统计上下文的优势，建立“当前上下文构成”和“本轮实际消耗归属”两个互不混淆的视角。

## What Changes

- 在模型调用前基于最终 `ModelRequest` 生成当前上下文快照，区分 system、conversation、tool results、tool definitions，以及能够可靠标记来源的 Skills、memory、RAG、attachments。
- 分类数据明确标记为本地估算；Provider 返回的 input/output/cache/reasoning usage 作为实际消耗，不用分类估算冒充计费值。
- 为一轮 Agent run 累计 Provider usage，并按 `lead_agent`、`subagent`、`middleware` 等 caller 归属；保留每次模型调用/执行步骤的 attribution，以支持调试展示和后续评测。
- 扩展 `/api/chat` 流式 `usage-update`、`context-update` 与 `finish.usage` 的向后兼容字段；现有 `input_tokens`、`output_tokens`、`total_tokens` 和上下文总量字段继续保留。
- chat 页将“当前 context window”与“本轮累计 usage”分开展示，并提供摘要与调试层级；无 Provider 明细或无可靠来源标记时安全降级为总量或 `other`，不伪造分类。
- 不改变模型选择、summarization 触发规则、计费规则或会话上下文文件面板。

## Capabilities

### New Capabilities

- `agent-token-observability`: 规定当前模型上下文构成、Provider 实际 usage、Agent caller/step attribution、估算精度与聚合语义。

### Modified Capabilities

- `platform-chat`: 将现有 `usage-update` 与 `context-update` 从总量展示扩展为向后兼容的分类和归属展示契约。

## Impact

- Harness：`backend/packages/harness/noesis/middlewares/context_metrics*`、模型调用上下文来源标记、子 Agent 与中间件调用身份。
- 平台流式层：`backend/noesis_server/domain/chat/streaming/` 的 usage 归一、累计和 SSE payload。
- 前端：chat SSE 类型、token/context 状态、顶部指标与调试明细 UI。
- API/SSE：仅扩展 `/api/chat` 现有事件字段，不删除或重命名既有字段，不构成破坏性变更。
- 测试：上下文分类、Provider detail 归一、caller/step 聚合、重复事件去重、前端兼容降级。
- 不引入新的外部运行时依赖；token 分类优先使用现有模型 tokenizer，缺失时使用 LangChain 近似计数。
