## ADDED Requirements

### Requirement: 系统 SHALL 以 RunEvent 作为一次 Agent run 的内部事件语言

系统 SHALL 定义结构化 RunEvent（或等价 typed 事件），至少覆盖：run 开始、文本/推理增量与结束、工具输入/输出、用量/上下文更新、业务扩展事件、**HITL 请求与暂停（hitl_pending）**、run 完成/中止/错误。

Agent 执行层与事件映射层 **SHALL** 向 RunEvent 总线发布上述事件，**SHALL NOT** 将 SSE 文本帧作为唯一或权威的内部事件表示。

#### Scenario: 执行层不直接产出 SSE 字符串作为总线事件

- **WHEN** 一次 Agent run 产生文本增量
- **THEN** 总线订阅方 SHALL 能收到结构化文本增量事件，且该事件在进入 SseDelivery 之前 **SHALL NOT** 必须已是 `event: text-delta` 字符串

#### Scenario: HITL interrupt 进入总线

- **WHEN** LangGraph 产生 human-in-the-loop interrupt
- **THEN** 总线 SHALL 发布 HitlRequired（或等价）事件，且随后可发布 RunPaused(reason=hitl_pending)；该序列 **SHALL NOT** 被建模为 RunCompleted

### Requirement: 系统 SHALL 提供可多订阅的 RunEvent 总线

对每个 `run_id`，系统 SHALL 支持多个 Delivery/Sink 并发订阅同一事件流（Fan-out）。Keepalive 注释帧若存在，SHALL 仅由 SseDelivery 在传输层注入，**SHALL NOT** 作为 RunEvent 广播给其它 sink。

#### Scenario: Persist 与 SSE 同时订阅

- **WHEN** 一次由 HTTP SSE 发起的 run 注册 PersistSink 与 SseDelivery
- **THEN** 二者 SHALL 都能观察到该 run 的完成类事件，且互不阻塞对方的订阅注册

### Requirement: PersistSink SHALL 独占流式 assistant 落库状态机

PersistSink SHALL 负责 assistant 消息的骨架插入、终态更新（completed / error / partial），并遵循既有「骨架—检查点—终态」互斥语义。落库 **SHALL NOT** 依赖浏览器 SSE 连接仍然存活。

消息或会话元数据 SHALL 记录 run 来源 `origin`（如 `web`、`telegram`、`wechat`、`cron`、`eval`）以便审计。

对 HITL：收到 HitlRequired / RunPaused(hitl_pending) 时，PersistSink **SHALL** 保持 assistant `status=streaming`（parts 可记 pending HITL），**SHALL NOT** 写入 completed / error / partial 终态。仅在 RunCompleted / RunAborted / RunError（含 HITL 超时 reject 后的终态路径）时终态落库。`hitl/resume` 续写 **SHALL** 使用同一 `assistant_message_id`。

#### Scenario: 无 SSE 订阅者仍终态落库

- **WHEN** 一次 run 仅注册 PersistSink（例如通道或评测发起）并正常完成
- **THEN** assistant 行 SHALL 被更新为终态 completed（或等价成功态），不得因缺少 SseDelivery 而保持 streaming

#### Scenario: 用户停止经统一生命周期

- **WHEN** 用户触发停止且 RunLifecycle 原因为用户停止
- **THEN** PersistSink SHALL 将 assistant 更新为 partial 并带上与现网一致的停止语义，且其它 sink SHALL 能收到中止/完成类事件

#### Scenario: hitl_pending 不终态

- **WHEN** 总线发布 HitlRequired 且本段传输以 hitl_pending 暂停
- **THEN** PersistSink SHALL NOT 将 assistant 标记为 completed，SHALL 保持可 resume 的 streaming/pending 状态

### Requirement: SseDelivery SHALL 对外保持既有 SSE 契约

SseDelivery SHALL 将 RunEvent 编码为现有 `POST /api/chat/sessions/stream` 所使用的 SSE 事件类型与 JSON 载荷形状，使既有前端 `useSSEStream` 在不修改事件分支的前提下可工作。

本能力 **SHALL NOT** 将替换 WebSocket 作为浏览器主实时通道列为必要条件。

#### Scenario: 文本增量帧兼容

- **WHEN** 总线发布文本增量 RunEvent 且 HTTP 客户端正在消费 SSE
- **THEN** 客户端 SHALL 收到与重构前兼容的 text-delta（或项目当前实现所用等价事件名）帧

### Requirement: 系统 SHALL 提供 ChannelAdapter SPI

系统 SHALL 定义可注册的 ChannelAdapter 接口，包含：`channel_type`、capabilities（是否支持流式编辑、长度限制、标记语言等）、生命周期 start/stop、将平台入站转为统一 InboundMessage、以及按 capabilities 将 RunEvent 投影为平台出站。

`channel_type` SHALL 至少预留 `telegram` 与 `wechat`（具体微信产品形态由实现 change 选定），并允许增加其它类型而不修改 Fan-out 核心。

#### Scenario: 按 capabilities 选择出站策略

- **WHEN** adapter 声明不支持 streaming_edit
- **THEN** 出站路径 SHALL 在 run 完成后再投递终态文本（可分段），**SHALL NOT** 要求平台支持逐 token edit

#### Scenario: 注册表可解析类型

- **WHEN** 配置或绑定指定 `channel_type=telegram`
- **THEN** ChannelRegistry SHALL 能解析到已注册的 adapter（或显式 stub），而非在 QaService 内硬编码平台分支

### Requirement: ChannelBinding SHALL 映射外部会话到 Noesis session

系统 SHALL 持久化绑定：`(user_id, channel_type, external_chat_id[, thread_id]) → session_id`。入站消息在执行 Agent 前 SHALL 解析到合法 `user_id` 与 `session_id`；未配对发送方 **SHALL NOT** 触发任意用户的特权 Agent 执行。

#### Scenario: 未配对拒绝

- **WHEN** 未绑定的外部账号向已启用通道发消息
- **THEN** 系统 SHALL 拒绝执行该用户的 Agent 任务（可回复配对引导）

### Requirement: 通道入站消息 SHALL 写入消息 SSOT

经 ChannelAdapter 入站的用户文本（及实现支持的附件元数据）SHALL 写入与 Web 聊天相同的会话消息存储，并带有通道来源与外部消息 id（若平台提供），使得浏览器通过历史消息 API 可见。

#### Scenario: 网页可见 TG 或微信来源消息

- **WHEN** 已配对用户经通道发送一条文本且落库成功
- **THEN** `GET` 该 `session_id` 的 messages SHALL 包含对应 user 消息

### Requirement: 通道出站 SHALL NOT 依赖浏览器 SSE 存活

ChannelDelivery 订阅 RunEvent 并向平台投递时，**SHALL NOT** 要求同一 `session_id` 上存在活跃的浏览器 SSE 连接。

#### Scenario: 仅通道在线完成投递

- **WHEN** run 由通道入站触发且无浏览器 SSE 订阅
- **THEN** 在 adapter 启用出站的前提下，用户仍 SHALL 能在该通道收到终态（或 capabilities 允许的流式）回复
