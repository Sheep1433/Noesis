# agent-delivery · Delta

## MODIFIED Requirements

### Requirement: RunEvent SHALL 具有单调 sequence

除传输 keepalive 与标记为 transient 的事件外，每个会影响 run 快照或客户端显示的 RunEvent SHALL 在该 run 内获得严格递增的 `sequence`。客户端与服务端 SHALL 使用 sequence 去重和检测缺口；不同 run 的 sequence 无需可比较。transient 事件 SHALL NOT 分配 sequence，也 SHALL NOT 参与缺口判定。

#### Scenario: sequence 严格递增

- **WHEN** 同一 run 依次产生 text delta、tool start 与 tool result
- **THEN** 后一 durable 事件的 sequence SHALL 大于前一事件

#### Scenario: keepalive 不推进 sequence

- **WHEN** SSE Delivery 在无业务事件期间发送 keepalive
- **THEN** run 的 last_sequence SHALL 保持不变

#### Scenario: transient 事件不占号

- **WHEN** 服务端以 transient 标记投递一条 text delta
- **THEN** 该事件 SHALL NOT 携带 sequence、SHALL NOT 进入重放缓存
- **AND** 后续 durable 事件的 sequence SHALL 不受该事件影响

### Requirement: SseDelivery 保持既有 SSE 契约

SseDelivery SHALL 将 RunEvent 编码为现网 stream 事件形状，并在新订阅或无法连续补发时支持 `run-snapshot` 与带 `sequence` 的业务事件。既有 reasoning/text/tool/HITL 分支 SHALL 保持兼容；终态 typed 事件（RunCompleted / RunAborted / RunError）SHALL 统一编码为 `run.finished`，`finish` / `abort` / `error` 编码名 SHALL 退役（终态载荷 usage / model_calls / finish_reason 并入 `run.finished`）；新增临时 run 状态 SHALL 使用 `run-status`，不得沿用会令旧客户端提前结束的终态 `error`。主会话 run 与子 Agent run 的 SSE 流 SHALL 使用同一事件词汇与同一编码实现，`GET /api/chat/runs/{run_id}/stream` SHALL 为单一端点实现，SHALL NOT 按 run origin 分叉事件方言。本能力 SHALL NOT 将替换 WebSocket 列为浏览器主通道必要条件。

#### Scenario: text-delta 兼容

- **WHEN** 总线发布文本增量且 HTTP 客户端在消费 SSE
- **THEN** 客户端 SHALL 收到兼容 `text-delta` 帧
- **AND** durable 帧 SHALL 携带可用于去重的 run sequence

#### Scenario: 临时模型错误不结束 SSE

- **WHEN** Agent 将重试模型流请求
- **THEN** SseDelivery SHALL 发出 `run-status` 且 `will_retry=true`
- **AND** SHALL NOT 因该临时错误发终态 `[DONE]`

#### Scenario: 子 Agent run 走同一编码

- **WHEN** 客户端订阅 origin=subagent 的 run 事件流
- **THEN** 收到的事件词汇 SHALL 与主会话 run 一致（帧级事件 + run 级生命周期事件）
- **AND** SHALL NOT 出现 `message.updated` 全量投影事件

## ADDED Requirements

### Requirement: 投递协议 SHALL 对主会话与子 Agent run 统一

主会话 run 与子 Agent run 的事件投递 SHALL 由同一投递内核提供语义：单调 sequence 分配、有界重放缓存、订阅 fanout、transient 旁路、快照降级恢复与订阅配额。订阅配额上限与 owner 不可达（503）行为 SHALL 对子 Agent run 同样生效；系统 SHALL NOT 为子 Agent run 维护第二套事件历史与订阅注册表。

#### Scenario: 子 run 订阅计入配额

- **WHEN** 用户订阅数达到 SSE 订阅上限后再订阅一个子 Agent run
- **THEN** 系统 SHALL 返回与主 run 相同的 429 语义
- **AND** SHALL NOT 为子 run 放宽配额

#### Scenario: 子 run 重放走连续性校验

- **WHEN** 客户端以 after_sequence 重连一个运行中的子 Agent run
- **THEN** 服务端 SHALL 在缓存可连续补齐时按 sequence 补发 durable 事件
- **AND** 无法补齐时 SHALL 返回权威 `run-snapshot`，与主 run 行为一致

#### Scenario: 平行事件总线退役

- **WHEN** 系统按 run origin 发布事件
- **THEN** 所有发布 SHALL 经由统一投递内核
- **AND** 代码中 SHALL NOT 存在仅服务子 Agent run 的独立事件历史/订阅注册表实现

### Requirement: transient 事件 SHALL 仅在线投递

投递协议 SHALL 定义 durable 与 transient 两类事件：durable 事件占 sequence、进重放缓存、可重放；transient 事件不占 sequence、不进缓存、仅投递给建立连接的在线订阅者。恢复模型 SHALL 只有一份：重连 = 权威快照 replace + durable 事件重放 + live 接收。发布侧的持久性策略差异（主链路 delta 为 durable、子 Agent 链路 delta 为 transient）SHALL 只存在于服务端发布点，客户端协议 SHALL 统一为「durable 必重放、transient 尽力而为、快照权威」。

#### Scenario: 断线期间的 transient 事件不补发

- **WHEN** 订阅者断线期间子 Agent run 产生 transient stats-update
- **THEN** 重连后该事件 SHALL NOT 被补发
- **AND** 客户端 SHALL 从权威快照或后续 durable 事件获得一致状态

#### Scenario: 主链路 delta 仍可重放

- **WHEN** 主会话 run 流式期间订阅者断线并快速重连
- **THEN** 服务端 SHALL 按缓存连续性补发断线期间的 durable delta 事件
- **AND** 现有多 Tab 对齐行为 SHALL 保持

### Requirement: run.finished SHALL 为唯一流终止事件

每个 run 的 SSE 流 SHALL 以 `run.finished`（载荷含 status / finish_reason / usage / model_calls）作为唯一终止信号，后随 `[DONE]`。主链路 typed 终态事件（RunCompleted / RunAborted / RunError）SHALL 编码为 `run.finished`；现 `finish` / `abort` / `error` 编码名 SHALL 退役，其载荷 SHALL 并入 `run.finished`（用户中止 → `status=interrupted, finish_reason=stopped`；错误 → `status=error`）。RunPaused(hitl_pending) SHALL 保持非终态语义，SHALL NOT 终止流。

#### Scenario: 主 run 正常完成

- **WHEN** 主会话 run 到达 completed
- **THEN** 流 SHALL 以 `run.finished(status=completed)` 结束并后随 `[DONE]`
- **AND** SHALL NOT 再以 `finish` / `abort` / `error` 编码名发送终态帧

#### Scenario: 用户中止

- **WHEN** 主会话 run 被用户停止
- **THEN** 流 SHALL 以 `run.finished(status=interrupted, finish_reason=stopped)` 结束
- **AND** 客户端 SHALL 无需依赖 `abort` 编码即可收口

#### Scenario: 子 run 终态同形

- **WHEN** 子 Agent run 到达任意终态
- **THEN** 终止信号 SHALL 为同词汇的 `run.finished`，与主 run 无差异
