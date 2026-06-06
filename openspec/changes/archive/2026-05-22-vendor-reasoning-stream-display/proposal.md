## Why

Noesis 前端已具备 `ReasoningBlock` 与 `reasoning-delta` 消费路径，但 `LangGraphSseBridge` 在 `on_chat_model_stream` 仅转发 `content` 为 `text-delta`，未从厂商原生字段提取思考流。Qwen（DashScope）、DeepSeek 等模型在 API 层提供 `reasoning_content`（或等价字段），与「正文混在 `text-delta` 里用 `<think>` 标签」的兜底方式并存，导致思考块展示不稳定、且与 `docs/dev/langchain-stream-demo-implementation.md` 约定不一致。需在统一 SSE 契约下，按 `MODEL_TYPE` 选用正确 LangChain 集成，并优先走原生 reasoning SSE，保留标签解析作为最终兜底。

## What Changes

- **`backend/core/llm_util.py`**：`MODEL_TYPE=deepseek` 使用 **`langchain-deepseek`** 的 `ChatDeepSeek`；`qwen` 继续使用 **`langchain-qwq`** 的 `ChatQwen`；`openai`、`minimax` 及其它未列类型使用 **`ChatOpenAI`**（兼容 OpenAI 协议网关）。
- **新增** `backend/core/reasoning_chunk_extractor.py`（或等价模块）：从 `AIMessageChunk` / `astream_events` 载荷中按厂商常见字段提取思考增量（首版以 **`additional_kwargs.reasoning_content`**、**`additional_kwargs.reasoning`** 为主；可扩展 `thinking` 等别名），与 `MODEL_TYPE` 解耦。
- **`LangGraphSseBridge`**：在 `on_chat_model_stream` 识别非空思考增量时发出 **`reasoning-start` / `reasoning-delta` / `reasoning-end`**（生命周期对齐 `docs/dev/langchain-stream-demo-implementation.md`：正文开始、工具开始、下一轮 LLM、流结束关闭 reasoning）；同步 `AssistantMessageBuilder.append_reasoning`。
- **Qwen 流式补全**：若 `langchain-qwq` 流式 chunk 未写入 `additional_kwargs.reasoning_content`，在仓库内以 **最小子类** 覆盖 `_convert_chunk_to_generation_chunk`（逻辑对齐 `langchain-deepseek` 对 `delta.reasoning_content` 的映射），避免 fork 第三方包。
- **前端**：`useSSEStream` 补齐 **`reasoning-start` / `reasoning-end`** 回调；`chat.vue` 在收到原生 reasoning 流时 **禁用** 同一段 `text-delta` 上的 `<think>` 拆标签（避免双写）；无原生 reasoning 时 **保持** 现有 `appendTextDeltaWithRedactedThinking` 兜底。
- **配置**：`ModelSettings.show_thinking_process` 为 `false` 时，桥接层 **不** 发出 `reasoning-*`（正文与工具流不变）。
- **文档**：更新 `docs/prd/platform/SSE流式数据设计.md` 思考流与厂商字段表；`docs/dev/redacted-thinking-inline-parsing.md` 标明兜底优先级；`docs/test/test_tdd_design.md` 补充 bridge golden 与前端解析测试点。
- **依赖**：`pyproject.toml` 增加 `langchain-deepseek`（版本与当前 LangChain 栈兼容）。
- **非目标**：历史列表默认展示 reasoning（仍可按现策略仅 text）；Anthropic `thinking` block 专用 UI；OpenAI o-series 全量适配；`phase-*` 与 reasoning 混用。

## Capabilities

### New Capabilities

- （无）归入既有 `chat-sessions-and-streaming`。

### Modified Capabilities

- `chat-sessions-and-streaming`：补充按厂商从 LangChain chunk 提取思考并映射为 `reasoning-*` SSE、LLM 工厂路由（qwen / deepseek / openai 系）、前端原生 reasoning 与 `<think>` 兜底的互斥优先级及 `show_thinking_process` 开关语义。

## Impact

- **后端**：`backend/core/llm_util.py`、`backend/core/reasoning_chunk_extractor.py`（新）、`backend/utils/langgraph_sse_bridge.py`、`backend/utils/message_builder.py`、`backend/config/env.py`（`.env.example` 补充 `MODEL_TYPE=deepseek`）、`backend/pyproject.toml` / lockfile。
- **前端**：`frontend/src/views/chat/useSSEStream.ts`、`frontend/src/views/chat.vue`、`frontend/src/views/chat/messageParts.ts`（兜底互斥标志）。
- **文档**：`docs/prd/platform/SSE流式数据设计.md`、`docs/dev/redacted-thinking-inline-parsing.md`、`docs/dev/langchain-stream-demo-implementation.md`（对齐实现状态）、`docs/test/test_tdd_design.md`。
- **测试**：`backend/tests/` bridge golden（reasoning-start/delta/end 序列）；可选前端单测。
- **兼容性**：**非 BREAKING**——仅新增/补全可选 `reasoning-*` 帧；未升级模型或未开启思考时行为与现网一致（仍可走标签兜底）。
