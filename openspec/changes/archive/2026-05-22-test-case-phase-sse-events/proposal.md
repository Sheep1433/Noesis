## Why

测试用例生成（`TEST_CASE_QA`）是分阶段人机协同的长任务（解析需求 → 生成测试点 → 等待用户确认 → 并行生成用例等），当前 SSE 仍以文本与工具帧为主，用户难以在无工具调用时感知「进行到哪一步」。需要在**不破坏现有统一协议**的前提下，为测试用例场景补充可机器解析、可持久化对齐的阶段进度事件，提升可观测性与产品信任感。

## What Changes

- 在 Noesis 标准 SSE `data:` JSON 中，为 **仅测试用例相关流**（`POST /api/chat/sessions/stream` 且 `qa_type=TEST_CASE_QA`，以及 `POST /api/chat/sessions/{session_id}/test-case/resume`）增加 **`phase-start`、`phase-delta`、`phase-end`** 三类事件（与现有 `reasoning-*`、`text-*`、`tool-*` 等并列；事件名与字段在规格中固定）。
- 后端在 `CaseCoordinator` / 测试用例 LangGraph 流水线关键节点发出上述事件；`LangGraphSseBridge`（或与之等价的统一出口）负责序列化，保证与现有帧格式、收尾 `[DONE]` 一致。
- 前端 `useSSEStream`（及测试用例相关 UI）**可选**消费阶段事件，用于展示阶段条或文案；未升级的前端对未知 `type` 应继续安全忽略（与本仓库已有「未知键忽略」惯例一致）。
- 契约测试：`test_langgraph_sse_bridge_contract`（或等价用例）增加阶段事件 golden 断言，防止静默破坏。
- **非目标（本变更不覆盖）**：故障运维 / 深度研究等其他 `qa_type` 的阶段事件；`phase-error` 单独形态（可与现有 `error` 帧关系在设计中说明）；Run/Redis 级重放。

## Capabilities

### New Capabilities

- （无）阶段事件作为聊天流式契约的增量，归入既有「聊天会话与流式」能力。

### Modified Capabilities

- `chat-sessions-and-streaming`：补充「测试用例流式阶段进度」相关要求——限定 `qa_type`/端点、`type` 与负载字段、`phase-delta` 的增量语义（如阶段内状态或附加上下文）、与 `AssistantMessageBuilder` / multipart 的可选快照策略边界。

## Impact

- **后端**：`backend/utils/langgraph_sse_bridge.py`、`backend/agent/case_generate/*`、`backend/services/qa_service.py`（若事件在编排层注入）、SSE 契约测试。
- **前端**：`frontend/src/views/chat/useSSEStream.ts`、测试用例对话相关展示（按需最小改动）。
- **依赖**：无新增运行时依赖预期。
- **兼容性**：新建事件类型；旧客户端若不识别可自行忽略。**非 BREAKING**：不删除或.rename 现有事件类型。
