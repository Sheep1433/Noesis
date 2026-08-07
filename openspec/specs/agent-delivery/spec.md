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

对每个 `run_id`，系统 SHALL 支持 PersistSink、一个或多个 SseDelivery、ChannelDelivery 等多个 Sink 并发订阅。RunEvent 总线与 producer SHALL 由 run 生命周期拥有，任一 Delivery 取消订阅或写失败 SHALL NOT 取消 producer 或其它 Sink。keepalive SHALL 仅由 SseDelivery 注入，SHALL NOT 广播为业务 RunEvent，也 SHALL NOT 推进 run sequence。

#### Scenario: Persist 与多个 SSE 同时订阅
- **WHEN** 同一 run 注册 PersistSink 与两个浏览器 SseDelivery
- **THEN** 三者 SHALL 都能观察到后续完成类事件

#### Scenario: 单个 SSE 断开不影响其它订阅
- **WHEN** 两个 SseDelivery 中一个断开
- **THEN** producer、PersistSink 与另一个 SseDelivery SHALL 继续工作

### Requirement: PersistSink 独占流式 assistant 落库

PersistSink SHALL 负责骨架插入、节流语义检查点与终态（completed / error / partial），遵循同一 assistant 身份与终态互斥。检查点 MAY 更新完整 parts 快照，但 SHALL NOT 按 token 更新正文。落库 SHALL NOT 依赖浏览器 SSE 存活。消息/run 元数据 SHALL 记录 `origin`（如 `web`、`telegram`、`cron`、`eval`）。

`hitl_pending` 时 SHALL 保持 assistant `streaming`；仅真实终态事件落库。resume SHALL 使用同一 `run_id` 与 `assistant_message_id`。服务重启恢复 SHALL 将不可继续的悬空 run 收口为 run `interrupted` + assistant `partial`，不得标记 completed。

#### Scenario: 无 SSE 仍终态
- **WHEN** 仅 PersistSink 的 run 完成
- **THEN** assistant SHALL 为 completed（或等价成功态）

#### Scenario: 重启悬空 run 不误标成功
- **WHEN** recovery 发现无执行 owner 的 running run
- **THEN** PersistSink 或 recovery service SHALL 将 assistant 收口为 partial
- **AND** SHALL NOT 写入 completed

### Requirement: SseDelivery 保持既有 SSE 契约

SseDelivery SHALL 将 RunEvent 编码为现网 stream 事件形状，并在新订阅或无法连续补发时支持 `run-snapshot` 与带 `sequence` 的业务事件。既有 reasoning/text/tool/HITL/finish 分支 SHALL 保持兼容；新增临时 run 状态 SHALL 使用 `run-status`，不得沿用会令旧客户端提前结束的终态 `error`。本能力 SHALL NOT 将替换 WebSocket 列为浏览器主通道必要条件。

#### Scenario: text-delta 兼容
- **WHEN** 总线发布文本增量且 HTTP 客户端在消费 SSE
- **THEN** 客户端 SHALL 收到兼容 `text-delta` 帧
- **AND** 帧 SHALL 携带可用于去重的 run sequence

#### Scenario: 临时模型错误不结束 SSE
- **WHEN** Agent 将重试模型流请求
- **THEN** SseDelivery SHALL 发出 `run-status` 且 `will_retry=true`
- **AND** SHALL NOT 因该临时错误发终态 `[DONE]`

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

### Requirement: Delivery 失败 SHALL 与 Agent run 终态隔离

SseDelivery 或 ChannelDelivery 的出站失败 SHALL 只影响该 Delivery，并记录可定位的 delivery 状态/日志。平台投递失败 SHALL NOT 把已经成功的 Agent run 改写为 error，也 SHALL NOT 阻止 PersistSink 完成终态落库。

#### Scenario: Telegram 发送失败不污染 run
- **WHEN** Agent 已完成但 Telegram 出站调用失败
- **THEN** run 与 assistant SHALL 保持 completed
- **AND** Telegram delivery SHALL 记录独立失败结果

### Requirement: RunEvent 临时错误与终态错误 SHALL 分离

RunEvent SHALL 能表达 `retrying`、`will_retry`、稳定 `error_code`、用户安全文案、attempt 与 max_attempts。临时错误 SHALL 可恢复为 `running`；只有重试耗尽或明确不可重试错误 SHALL 发布终态 RunError。

#### Scenario: 重试后恢复
- **WHEN** 模型流第一次断开且自动重试成功
- **THEN** 总线 SHALL 先发布 retrying 状态再发布 running 状态
- **AND** 最终 SHALL 允许 run 正常 completed

### Requirement: RunEvent 总线 SHALL 按 Sink 类型实施背压

每个 subscriber 队列与 run event buffer SHALL 同时具有事件数和估算字节数上限。事件发布 SHALL NOT 顺序等待所有 Delivery。

PersistSink SHALL NOT 静默丢弃状态或终态；终态 SHALL 通过可靠 compare-and-set 路径持久化。SseDelivery 队列溢出时 SHALL 只断开该慢订阅者，使其通过 snapshot 恢复。ChannelDelivery 溢出或发送失败 SHALL 记录独立 delivery failure，不得阻塞 producer 或污染 run 终态。

#### Scenario: 慢 SSE 消费者队列溢出
- **WHEN** 某个 SSE subscriber 持续慢于 producer 并达到队列上限
- **THEN** 系统 SHALL 断开该 subscriber 并记录 overflow 原因
- **AND** producer、PersistSink 与其它 subscriber SHALL 继续工作

#### Scenario: PersistSink 暂时不可写
- **WHEN** PostgreSQL 检查点写入暂时失败但尚未达到 persistence timeout
- **THEN** 系统 SHALL 进行有界重试或合并为最新待写 snapshot
- **AND** SHALL NOT 无界缓存每个 token event

#### Scenario: PersistSink 持续不可写
- **WHEN** PostgreSQL 持续不可写并达到 persistence timeout
- **THEN** 系统 SHALL 停止继续生成不可保存的无限内容
- **AND** SHALL 使用受控错误将 run 收口为 error 或已有正文对应的 partial

### Requirement: feishu ChannelAdapter SHALL 可真实收发
`channel_type=feishu` SHALL 注册可执行 Adapter，而非 Stub 或仅可保存的配置类型。入站 SHALL 规范化为统一 InboundMessage 并进入 ChannelRunService；出站 SHALL 消费同一次 run 的 RunEvent，且 SHALL NOT 经过浏览器 SSE 字符串转发。

#### Scenario: Registry 解析飞书 Adapter
- **WHEN** 飞书运行时启动且存在有效启用配置
- **THEN** ChannelRegistry SHALL 解析到支持入站规范化与出站投影的 `feishu` Adapter

### Requirement: 飞书绑定 SHALL 分离授权主体与回复目标
飞书入站授权 SHALL 绑定发送者 open_id；群聊 chat_id 或 message_id SHALL 只作为会话线程与回复目标，SHALL NOT 单独授予群内所有成员调用 Agent 的权限。

#### Scenario: 同群未配对成员提及机器人
- **WHEN** 未配对成员在已存在目标 chat_id 的群中 @机器人
- **THEN** 系统 SHALL 拒绝触发 Agent

### Requirement: 飞书消息 SHALL 共用消息 SSOT 与 delivery 终态
飞书入站用户消息与 assistant 结果 SHALL 写入网页使用的同一消息 SSOT并记录 `origin=feishu`。飞书发送结果 SHALL 与 run 终态分离，断开浏览器或飞书发送失败 SHALL NOT 阻止 PersistSink 完成终态落库。

#### Scenario: 网页查看飞书会话
- **WHEN** 已配对用户通过飞书完成一轮对话
- **THEN** 对应 session 的 messages API SHALL 返回该轮 user 与 assistant 消息
- **AND** 消息来源 SHALL 可审计为 `feishu`

### Requirement: Delivery SHALL 提供通道运行健康 read model
Delivery SHALL 为设置控制面提供用户作用域的 adapter 状态、最近检查、最近入站/出站结果和脱敏错误摘要。该 read model SHALL 由真实 adapter/runtime 状态派生；通道配置 Service SHALL NOT 写入伪运行状态。

#### Scenario: 获取当前用户通道健康
- **WHEN** 设置服务请求当前用户通道健康摘要
- **THEN** Delivery SHALL 仅返回该用户通道的状态且不包含 token、外部请求 header 或内部堆栈

### Requirement: Delivery SHALL 支持受控测试投递
Delivery SHALL 接受已鉴权设置服务发起的测试投递命令，向指定当前用户通道发送固定测试内容，并返回稳定投递结果。测试投递 SHALL NOT 创建用户聊天消息或触发 Agent run。

#### Scenario: 测试投递成功
- **WHEN** 设置服务对健康且启用的通道发起测试投递
- **THEN** Delivery SHALL 发送固定内容、记录结果并返回关联 id
