## Why

Noesis chat 页已支持工具调用与流式正文，但用户无法感知「工具跑了多久、这条回复一共消耗多少 LLM token」。PRD 虽在 `finish.usage` 预留字段，当前 `LangGraphSseBridge` 与 `base_agent` 实际下发空 usage，前端 `onUsageUpdate` 亦为空实现。多步 Agent（工具 + 多轮 LLM）场景下，缺少这两项关键可观测信息，不利于排障与成本感知。

## What Changes

- **后端 `LangGraphSseBridge`**：在 `on_tool_start` / `on_tool_end` 计时，于 `tool-output-available` 增加可选字段 **`durationMs`**（单次工具调用耗时，毫秒）。
- **后端 `LangGraphSseBridge`**：从 `on_chat_model_end` 或 stream 末 chunk 的 `usage_metadata` 提取 token，按 **`run_id` 去重** 后**累计**到本轮 assistant 消息；每完成一轮 LLM 发出 **`usage-update`** SSE 事件；`finish.usage` 写入累计值（`input_tokens` / `output_tokens` / `total_tokens`）。
- **后端 `base_agent`**：移除固定空 `usage` 的 `__tw_finish__` 占位逻辑，改由 bridge 在 finalize 时填充真实累计 usage（无 provider 数据时仍可为空对象）。
- **消息持久化**：tool part 写入 `durationMs`；assistant 消息 `extra.usage` 写入累计 token（与 `finish` 一致）。
- **前端 chat 页**：`ToolCallCollapse` 展示单次工具耗时；assistant 气泡底部（或 `AssistantReplyToolbar` 上方）展示**整条回复累计** token；流式过程中随 `usage-update` 刷新，finish 后定格。
- **文档**：更新 `docs/prd/platform/SSE流式数据设计.md` 中 `tool-output-available`、`finish.usage` 及新增 `usage-update` 约定；补充 `docs/test/test_tdd_design.md` 测试点。
- **非目标**：TTFT、会话级累计 token、费用估算、每轮 LLM 逐步明细 UI、LangChain AgentMiddleware 作为主数据通路。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `chat-sessions-and-streaming`：补充工具耗时与消息级累计 LLM token 的 SSE 字段、桥接层提取与去重规则、前端展示与历史恢复语义。

## Impact

- **后端**：`backend/utils/langgraph_sse_bridge.py`、`backend/agent/base/base_agent.py`、`backend/utils/message_builder.py`（tool part / extra）、`backend/services/qa_service.py`（finish 落库）；可选 pytest golden 扩展。
- **前端**：`frontend/src/views/chat/useSSEStream.ts`、`frontend/src/views/chat/messageParts.ts`、`frontend/src/views/chat.vue`、`frontend/src/components/ToolCallCollapse/`、`frontend/src/components/AssistantReplyToolbar/`（或等价 metrics 行）。
- **API**：`POST /api/chat/sessions/stream` 及同桥接路径的 SSE 帧扩展；**非 BREAKING**（新增可选 JSON 键与可选事件类型，旧前端可忽略）。
- **文档**：`docs/prd/platform/SSE流式数据设计.md`、`docs/test/test_tdd_design.md`。
