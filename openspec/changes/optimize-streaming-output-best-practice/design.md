## Context

Noesis（智枢）当前流式链路以 `QaService` 直接消费各 Agent / Coordinator 的 async generator 为主，并通过 `_iter_agent_items_with_keepalive` 在等待上游下一帧超时时输出 SSE 注释保活帧。该实现对普通流式问答可用，但在 `TEST_CASE_QA` 这种多阶段长任务中，保活逻辑需要手动驱动 `agen.__anext__()`；当 Langfuse 追踪启用并依赖 `ContextVar` 时，历史上出现过 generator 在一个 Task 中创建 context token、在另一个 Task 中 reset 的异常。

当前修复通过 `asyncio.shield + pending Future` 降低了跨 Task 驱动风险，并把 Langfuse workflow context 上移到 `qa_service` 消费侧。但这仍然把“业务生产者执行”和“SSE 传输层保活”耦合在同一个迭代器包装器内，后续如果增加断线重连、多订阅者、跨端复用或更多长任务，会继续放大复杂度。

本变更采用生产者/消费者解耦：Agent 在独立 Task 内运行并发布事件，HTTP SSE 端只订阅事件；心跳来自订阅等待超时，而不是来自驱动业务 generator 超时。

## Goals / Non-Goals

**Goals:**

- 将平台级流式输出改为事件中介模型：生产者发布 `StreamEvent`，消费者订阅并格式化为现有 SSE 文本帧。
- SSE 注释保活只在订阅等待超时时产生，不再直接等待或取消上游 `agen.__anext__()`。
- 保持 `/api/chat/sessions/stream` 和 `/api/chat/sessions/{session_id}/test-case/resume` 的对外 SSE 协议兼容。
- 让 Langfuse `ContextVar` 生命周期绑定到生产者 Task，避免跨 Task reset。
- 保留现有 assistant 骨架落库、流式检查点、取消生成、错误帧和 `[DONE]` 收尾语义。

**Non-Goals:**

- 不引入第二套前端 SSE 协议，不要求前端识别新的心跳业务事件。
- 不在本变更中实现分布式 Redis StreamBridge；首版使用进程内 `asyncio.Queue` / `asyncio.Condition` 即可。
- 不重写 `LangGraphSseBridge` 的业务帧转换契约；它仍负责把 LangGraph/LangChain 事件转换为 Noesis `data:` JSON。
- 不改变 `TEST_CASE_QA` 的阶段 A/B 业务流程、RAG 策略或测试助手 UI 状态机。

## Decisions

### Decision 1: 引入进程内 StreamBridge 作为平台流式中介

新增 `backend/utils/stream_bridge.py`（或等价模块），定义：

- `StreamEvent`：包含 `kind`、`payload` 或直接承载上游 item；
- `HEARTBEAT_SENTINEL`：订阅超时时返回的心跳哨兵；
- `END_SENTINEL`：生产者正常结束哨兵；
- `MemoryStreamBridge.publish(run_id, item)`：生产者发布业务 item；
- `MemoryStreamBridge.publish_error(run_id, exc)`：生产者发布异常；
- `MemoryStreamBridge.publish_end(run_id)`：生产者结束；
- `MemoryStreamBridge.subscribe(run_id, heartbeat_interval)`：消费者订阅，超时产出 `HEARTBEAT_SENTINEL`。

理由：Queue/Condition 模型把“有没有事件可读”和“业务是否仍在运行”分离，SSE 心跳只观察空闲时间，不驱动 Agent 执行。

替代方案：继续使用 `shield + pending Future`。该方案改动小，但仍需手动维护 `__anext__` 的 pending 状态，长期不适合作为平台级流式基础设施。

### Decision 2: 生产者 Task 内包裹 Langfuse 上下文并消费上游 generator

`QaService` 为每次流式请求创建生产者 Task：

1. 构建当前请求的 Langfuse RunnableConfig；
2. 在生产者 Task 内进入 `langfuse_workflow_context`；
3. `async for raw in agent_generator` 顺序消费上游；
4. 将 raw 发布到 `StreamBridge`；
5. 异常发布为错误项，最后发布 end。

理由：生产者 Task 的生命周期完整覆盖上游 generator 的进入、yield 后恢复和退出，`ContextVar.set/reset` 在同一 Task 上闭合。

替代方案：仍在外层 SSE consumer 包裹 context。该方案对当前单 consumer 可用，但一旦 producer/consumer 解耦，追踪上下文应跟随业务执行侧，而不是 HTTP 订阅侧。

### Decision 3: SSE consumer 只订阅 bridge 并格式化输出

`QaService.exec_query` 与 `exec_test_case_resume` 中，SSE 侧循环改为：

- `HEARTBEAT_SENTINEL` → 输出 `: keepalive\n\n`；
- 业务 item → 交给 `LangGraphSseBridge.process_item(...)` 生成现有 SSE 帧；
- producer error → 走现有异常 SSE 与落库错误路径；
- `END_SENTINEL` → 执行 `bridge.finalize()` 并落库完成状态。

理由：HTTP 层只关心连接传输、格式化和持久化检查点，不关心上游 generator 的推进细节。

替代方案：在 Agent 内主动 yield 心跳。该方案不可行，因为 Agent 在等待 LLM / 工具 IO 时自身控制流挂起，无法主动定时 yield；同时会把 HTTP 传输细节污染到业务 Agent。

### Decision 4: `CaseCoordinator` 保持业务协调器职责

`CaseCoordinator.run_agent()` / `resume_agent()` 继续产出 dict 业务事件，但不得包含 SSE 传输保活逻辑，也不得依赖 `QaService` 通过多 Task 调用 `__anext__()`。阶段帧 `phase-*`、`testpoints-confirm-required`、`scene-cases` 仍由它按现有业务规则产出。

理由：`CaseCoordinator` 是 `TEST_CASE_QA` 业务状态机入口，不应承担 HTTP 长连接保活或 Langfuse 传输上下文管理。

替代方案：为 `CaseCoordinator` 单独实现 Queue。该方案会形成场景特化基础设施，违背平台流式能力统一复用目标。

### Decision 5: 首版不支持断线重放，但预留事件模型

首版 `StreamBridge` 可只服务单次 HTTP 连接，不要求持久 event log 或 Last-Event-ID 重放。`StreamEvent` 字段保留 `id` 的扩展空间，但任务中不实现多订阅者重放。

理由：当前 Noesis 前端没有基于 Last-Event-ID 的重连恢复需求；先解决保活与 ContextVar 架构问题，避免引入过大迁移面。

替代方案：一次性实现 event log / 断线重放。该方案更完整，但涉及内存保留、清理策略和重连契约，超出本次“流式输出最佳实践优化”的必要范围。

## Risks / Trade-offs

- [Risk] 生产者 Task 异常没有正确传回 SSE consumer，导致连接空转 → Mitigation：`publish_error` 与 `publish_end` 必须在 `finally` 中成对处理，测试覆盖异常传播。
- [Risk] 客户端断开后生产者继续运行浪费资源 → Mitigation：SSE consumer 的 `finally` 中取消 producer Task，并调用各 Agent 既有 `cancel_task` / 清理逻辑。
- [Risk] 双层桥接导致落库时序变化 → Mitigation：保留现有 `LangGraphSseBridge` 和 `AssistantMessageBuilder` 调用点，先只替换上游 item 获取方式。
- [Risk] 进程内 StreamBridge 不支持多进程共享 → Mitigation：明确首版仅覆盖单进程运行；分布式部署需要后续独立变更引入 Redis 或外部 broker。
- [Risk] 心跳帧频率过高增加无意义写入 → Mitigation：继续使用 `SSE_KEEPALIVE_INTERVAL_SECONDS` 配置，0 表示关闭，默认值沿用当前实现。

## Migration Plan

1. 新增 `MemoryStreamBridge` 单元测试，先覆盖 publish/subscribe/end/error/heartbeat。
2. 在 `QaService` 内新增私有 producer helper，并只切换 `TEST_CASE_QA` 首轮与 resume 路径验证行为。
3. 通过测试后，将通用 `exec_query` 的上游消费统一切到 bridge 模式，保留原 `_iter_agent_items_with_keepalive` 至迁移完成后删除。
4. 补充 Langfuse ContextVar 回归测试：开启 tracing，模拟上游长时间无输出与心跳，不应出现 reset 异常。
5. 运行 `uv run app.py` 验证后端基本启动；按影响范围运行后端 pytest。

回滚策略：保留原 `shield + pending Future` 迭代器直到 bridge 路径测试通过；若新路径出现阻断问题，可在 `QaService` 局部回退到旧消费方式，不影响前端协议。

## Open Questions

- 是否需要本次一并支持 Last-Event-ID 断线重放？当前建议不做。
- `StreamBridge` 是否放在 `utils` 还是新建 `runtime` 包？当前建议先放 `backend/utils/stream_bridge.py`，避免引入新的架构层。
- 通用 `COMMON_QA` / `FAULT_OPERATION_QA` / `DEEP_RESEARCH_QA` 是否一次性全量切换？当前建议先实现统一 helper，然后按测试覆盖一次性切换，避免两套保活长期并存。
