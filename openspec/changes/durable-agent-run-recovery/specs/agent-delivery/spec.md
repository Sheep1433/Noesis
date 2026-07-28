## MODIFIED Requirements

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

## ADDED Requirements

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
