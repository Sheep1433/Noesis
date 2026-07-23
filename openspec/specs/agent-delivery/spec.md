# agent-delivery Specification

## Purpose

本能力规定一次 Agent run 的 **Delivery Fan-out**：内部 `RunEvent` 语言与多订阅总线、PersistSink 落库、SseDelivery（浏览器 SSE）、ChannelAdapter SPI 与绑定、以及 Telegram 等通道运行时。配置/密钥/设置 UI 属于用户设置面；本能力只消费已持久化配置。代码锚点：`domain/chat/delivery/`、`services/channel_run_service.py`。

## Requirements

### Requirement: RunEvent 为内部事件语言

系统 SHALL 定义结构化 RunEvent，至少覆盖：run 开始、文本/推理增量与结束、工具输入/输出、用量/上下文、HITL 请求与暂停（`hitl_pending`）、完成/中止/错误。

执行层 **SHALL NOT** 将 SSE 文本帧作为唯一权威内部表示。

#### Scenario: HITL 进入总线

- **WHEN** LangGraph 产生 HITL interrupt
- **THEN** 总线 SHALL 发布 HitlRequired（或等价），且可继以 RunPaused(reason=hitl_pending)；**SHALL NOT** 建模为 RunCompleted

### Requirement: 可多订阅的 RunEvent 总线

对每个 `run_id`，系统 SHALL 支持多个 Sink 并发订阅。keepalive **SHALL** 仅由 SseDelivery 注入，**SHALL NOT** 广播为业务 RunEvent。

#### Scenario: Persist 与 SSE 同时订阅

- **WHEN** HTTP SSE run 注册 PersistSink 与 SseDelivery
- **THEN** 二者 SHALL 都能观察到完成类事件

### Requirement: PersistSink 独占流式 assistant 落库

PersistSink SHALL 负责骨架插入与终态（completed / error / partial），遵循骨架—检查点—终态互斥。落库 **SHALL NOT** 依赖浏览器 SSE 存活。消息/会话元数据 SHALL 记录 `origin`（如 `web`、`telegram`、`cron`、`eval`）。

`hitl_pending` 时 **SHALL** 保持 `streaming`；仅终态事件落库。resume **SHALL** 使用同一 `assistant_message_id`。

#### Scenario: 无 SSE 仍终态

- **WHEN** 仅 PersistSink 的 run 完成
- **THEN** assistant SHALL 为 completed（或等价成功态）

### Requirement: SseDelivery 保持既有 SSE 契约

SseDelivery SHALL 将 RunEvent 编码为现网 stream 事件形状，使 `useSSEStream` 无需改事件分支即可工作。**SHALL NOT** 将替换 WebSocket 列为浏览器主通道必要条件。

#### Scenario: text-delta 兼容

- **WHEN** 总线发布文本增量且 HTTP 客户端在消费 SSE
- **THEN** 客户端 SHALL 收到兼容 text-delta（或现行名）

### Requirement: ChannelAdapter SPI 与 Binding

系统 SHALL 定义 ChannelAdapter（`channel_type`、capabilities、入站规范化、出站投影）与 ChannelRegistry。`channel_type` SHALL 至少支持 `telegram`（`wechat` MAY 预留）。

ChannelBinding SHALL 持久化 `(user_id, channel_type, external_chat_id[, thread_id]) → session_id`。未配对发送方 **SHALL NOT** 触发任意用户的特权 Agent 执行。

#### Scenario: 未配对拒绝

- **WHEN** 未绑定账号向已启用通道发消息
- **THEN** 系统 SHALL 拒绝执行 Agent（可回复配对引导）

### Requirement: 通道消息写入 SSOT

通道入站用户文本 SHALL 写入与 Web 相同的消息存储；出站 **SHALL NOT** 要求同 session 存在浏览器 SSE。

#### Scenario: 网页可见 TG 消息

- **WHEN** 已配对用户经 Telegram 发送文本且落库成功
- **THEN** 该 `session_id` 的 messages API SHALL 包含对应用户消息

#### Scenario: 仅通道在线

- **WHEN** run 由通道触发且无浏览器 SSE
- **THEN** 用户仍 SHALL 能在通道收到终态（或 capabilities 允许的流式）回复

### Requirement: 通道配置归属 settings；运行时消费配置

通道 CRUD、密钥、配对与设置 UI **SHALL NOT** 在 Delivery 内另建一套；运行时 SHALL 读取已持久化配置。Telegram adapter 在启用时 SHALL 可真收发（测试可用 stub 替换）。

#### Scenario: 启用后可解析

- **WHEN** 用户启用 `telegram` 且 pairing 有效
- **THEN** Registry SHALL 解析到可执行 adapter，入站/出站不经 SSE 字符串 yield

### Requirement: 出站尊重 capabilities

不支持 `streaming_edit` 的通道 SHALL 在 run 完成后投递终态文本；默认避免把完整工具细节镜像到 IM（除非 adapter 显式启用）。

#### Scenario: 无 edit 则终态

- **WHEN** adapter 声明 `streaming_edit=false` 且 run 完成
- **THEN** 通道内容 SHALL 基于终态文本投影
