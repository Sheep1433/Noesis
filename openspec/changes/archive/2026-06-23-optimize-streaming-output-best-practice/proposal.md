## Why

当前流式输出保活逻辑直接驱动上游 async generator，在 `TEST_CASE_QA` 与 Langfuse `ContextVar` 叠加时曾触发跨 Task reset 异常；虽然已通过 `shield + pending Future` 修复崩溃，但实现仍耦合了“Agent 执行驱动”和“SSE 传输保活”两个职责。现在需要将流式链路优化为生产者/消费者解耦模型：生产者独立运行、消费者订阅事件、心跳只发生在传输层等待超时处。

## What Changes

- 引入平台级流式事件中介（如 `StreamBridge` / Queue-backed bridge），让 Agent/Coordinator 将事件发布到桥接层，SSE 响应从桥接层订阅并格式化输出。
- 将 SSE 注释保活从“等待 `agen.__anext__()` 超时”调整为“订阅桥接层事件超时”，避免保活逻辑直接驱动业务 async generator。
- 保持 `/api/chat/sessions/stream` 与 `/api/chat/sessions/{session_id}/test-case/resume` 的外部 SSE 契约兼容：业务 `data:` JSON、`phase-*`、`finish`、`data: [DONE]` 等不做破坏性改动。
- 保留标准 SSE 注释心跳（如 `: keepalive\n\n`），前端继续按 SSE 注释行忽略，不新增前端业务事件类型。
- 调整 Langfuse 上下文包裹位置，确保生产者 Task 内的追踪上下文生命周期完整，不再依赖跨 Task 驱动 generator 的约束。
- 补充平台流式桥接与 `TEST_CASE_QA` resume 路径回归测试，覆盖心跳、结束哨兵、异常传播、取消与 ContextVar 不跨 Task reset。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `platform-chat`: 修改平台级 SSE 传输稳定性与保活实现契约，引入事件中介/订阅式流式输出要求，但保持现有 `/api/chat` SSE 对外协议兼容。
- `agent-test-case`: 修改 `TEST_CASE_QA` 与 `test-case/resume` 的流式执行约束，要求 `CaseCoordinator` 不再由 SSE 保活逻辑直接跨 Task 驱动，阶段业务帧经平台流式中介转发。

## Impact

- 后端：主要影响 `backend/services/qa_service.py`、`backend/agent/case_generate/case_coordinator.py`、`backend/utils/langgraph_sse_bridge.py` 附近的流式组织方式，可能新增 `backend/utils/stream_bridge.py` 或等价模块。
- API/SSE：涉及 `POST /api/chat/sessions/stream` 与 `POST /api/chat/sessions/{session_id}/test-case/resume`；不引入 breaking change，心跳仍为 SSE 注释行，业务帧 JSON 兼容现有前端。
- 可观测性：Langfuse 追踪继续可选启用，ContextVar 生命周期绑定到生产者 Task，追踪失败仍不得阻塞主链路。
- 测试：新增或调整后端 pytest，覆盖 StreamBridge 订阅超时心跳、结束、异常、取消、Langfuse ContextVar 回归，以及 `TEST_CASE_QA` 首轮与 resume 的 SSE golden 行为。
