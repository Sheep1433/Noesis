## ADDED Requirements

### Requirement: 流式问答入口 SHALL 经 Run Fan-out 投递

`POST /api/chat/sessions/stream`（及设计文档中约定的同前缀端点）SHALL 通过 RunEvent 总线的 SseDelivery 向客户端输出 SSE，且 SHALL 注册 PersistSink 以完成 assistant 落库。问答编排 **SHALL NOT** 在单一 generator 内将「成帧字符串」作为落库与多端扩展的唯一事件源。

对外 SSE 事件类型与载荷形状 SHALL 保持与重构前兼容（见既有流式契约要求）；本要求 **SHALL NOT** 引入对前端的强制破坏性变更。

#### Scenario: HTTP 流式仍返回 text/event-stream

- **WHEN** 已认证用户对 `POST /api/chat/sessions/stream` 发起合法流式请求
- **THEN** 响应 SHALL 为 `text/event-stream`，并包含与现网兼容的增量与结束帧（含 `[DONE]` 收尾约定）

#### Scenario: 落库不依赖客户端读完流

- **WHEN** 流式 run 成功完成且客户端在收齐全部帧之前断开
- **THEN** 系统仍 SHALL 按既有断连/终态规则更新 assistant 行，且该逻辑由统一落库 sink/生命周期驱动而非仅依赖 generator finally 的临时分支复制

### Requirement: 停止生成 SHALL 走统一 Run 生命周期

`POST`（或既有）停止生成接口 SHALL 通过统一 RunLifecycle/cancel 入口通知正在进行的 run，使 PersistSink 与仍订阅的 Delivery 观察到一致的中止语义；**SHALL NOT** 长期保留与 Fan-out 无关的第三套仅适用于旧 generator 的停止状态机（过渡期除外）。

#### Scenario: 停止后 partial 落库

- **WHEN** 用户在流式进行中调用停止接口
- **THEN** assistant 消息 SHALL 进入 partial（或等价）终态，并与现网停止文案/finish_reason 约定兼容

### Requirement: HITL 分段流 SHALL 经同一 Fan-out

`hitl-required` / `finish_reason=hitl_pending` 与 `POST .../hitl/resume` 返回的新 SSE **SHALL** 经同一 RunEvent → SseDelivery / PersistSink 路径，语义与主规格 `platform-chat` HITL 要求一致：pending 不 completed；resume 续写同一 `assistant_message_id`；**SHALL NOT** 在 Fan-out 外另起一套仅 generator 内可见的 HITL 落库分支（过渡期除外）。

#### Scenario: resume 仍走 Fan-out

- **WHEN** 用户对 pending HITL 调用 `hitl/resume`
- **THEN** 响应 SHALL 为经 SseDelivery 编码的 `text/event-stream`，且 PersistSink SHALL 继续更新同一 assistant 行直至真正终态
