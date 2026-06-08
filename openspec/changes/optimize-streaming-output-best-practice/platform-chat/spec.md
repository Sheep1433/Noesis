## Purpose

本 delta 规定平台聊天流式输出的最佳实践改造：将 Agent 事件生产与 HTTP SSE 订阅传输解耦，使心跳仅作为订阅空闲超时的 SSE 注释帧输出，避免传输层保活逻辑直接驱动业务 async generator。

## ADDED Requirements

### Requirement: 流式输出 SHALL 通过事件中介解耦生产者与 SSE 消费者

系统 SHALL 为 `POST /api/chat/sessions/stream` 及共用同一流式基础设施的聊天端点提供进程内事件中介（如 Queue-backed `StreamBridge`），使 Agent / Coordinator 在生产者 Task 内发布业务事件，SSE 响应在消费者 Task 内订阅事件并格式化为既有 SSE 文本帧。

生产者 Task SHALL 负责顺序消费上游 Agent async generator，并发布业务 item、异常 item 与结束哨兵；SSE 消费者 SHALL NOT 通过每次心跳等待直接创建新 Task 驱动 `agen.__anext__()`。

#### Scenario: 生产者发布业务事件后消费者输出既有 SSE 帧

- **WHEN** Agent 生产者向事件中介发布一条 LangGraph 或业务 dict 事件
- **THEN** SSE 消费者 SHALL 通过现有 `LangGraphSseBridge` 或等价格式化入口输出与改造前兼容的 `data:` JSON 帧
- **AND** 前端 `useSSEStream` SHALL 不需要识别新的业务事件类型才能继续消费

#### Scenario: 上游异常通过事件中介传递

- **WHEN** 生产者 Task 消费 Agent 流时发生未处理异常
- **THEN** 系统 SHALL 将异常传递给 SSE 消费者并发出既有 `error` / `finish` / `[DONE]` 语义
- **AND** 系统 SHALL 按现有错误落库策略将 assistant 消息标记为错误

### Requirement: Langfuse ContextVar 生命周期 SHALL 绑定业务生产者 Task

当 `LANGFUSE_TRACING_ENABLED` 为真时，系统 SHALL 在业务生产者 Task 内建立 Langfuse workflow context，并在同一 Task 内完成上游 Agent async generator 的进入、迭代与退出。SSE 订阅心跳逻辑 SHALL NOT 造成 Langfuse `ContextVar` token 在不同 Task 间 reset。

#### Scenario: 心跳期间不触发 ContextVar 跨 Task reset

- **WHEN** Langfuse 追踪开启且上游 Agent 在超过保活间隔内没有产出业务事件
- **THEN** 系统 SHALL 继续发送 SSE 注释保活帧
- **AND** 后续上游事件或流结束时 SHALL NOT 抛出 `ContextVar` token created in a different Context 类异常

## MODIFIED Requirements

### Requirement: 服务端 SHALL 按可配置间隔发送 SSE 注释保活帧

在 `POST /api/chat/sessions/stream` 及共用同一桥接层的其它流式聊天端点的流式响应中，当距离上一次已写入客户端的字节（含任意业务 SSE 帧或注释保活帧）超过配置阈值且流仍未正常结束时，系统 SHALL 写入一条符合 SSE 规范的注释行帧（以 `:` 开头、以空行结束），且该帧 SHALL 不包含 `event:` 或业务 `data:` JSON，以免干扰现有前端解析。

保活帧 SHALL 由 SSE 消费者订阅事件中介时的空闲超时产生；系统 SHALL NOT 为了发送心跳而取消、重启或跨 Task 反复驱动上游 Agent async generator 的 `__anext__()`。

#### Scenario: 保活间隔为正且上游长时间无输出

- **WHEN** `sse_keepalive_interval_seconds`（环境变量 `SSE_KEEPALIVE_INTERVAL_SECONDS`）为大于 0 的数值，且事件中介在超过该秒数内未收到下一条业务事件或结束哨兵
- **THEN** 系统 SHALL 向响应流写入至少一条 SSE 注释保活帧，随后继续订阅事件中介直至收到业务事件、错误、结束或取消信号

#### Scenario: 保活被显式关闭

- **WHEN** 配置项将保活间隔设为 0（或项目约定的「关闭」值）
- **THEN** 系统 SHALL 不发送注释保活帧，流行为与关闭该功能时一致

#### Scenario: 心跳不得驱动或取消上游业务生成器

- **WHEN** 上游 Agent 正在等待 LLM、工具调用或 RAG IO，且短时间内没有可发布的业务事件
- **THEN** SSE 心跳超时 SHALL 只影响消费者输出注释帧
- **AND** 上游业务生成器 SHALL 继续在生产者 Task 内运行，不得因心跳超时被 `wait_for` 取消
