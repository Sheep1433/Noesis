# platform-chat · Delta

## MODIFIED Requirements

### Requirement: 流式问答与 SSE 核心契约

浏览器实时响应 SHALL 使用 `/api/chat` 下的 run 创建与 SSE 订阅端点。系统 SHALL 提供独立的 run 创建、状态查询、SSE 订阅和停止能力，并 SHALL 删除 `POST /api/chat/sessions/stream`。浏览器主实时通道仍为 SSE，不要求 WebSocket。

事件类型至少覆盖：`run-snapshot`、`run-status`、`run.started`、`run.finished`、`approval.required`、`approval.resumed`、`reasoning-*`、`text-*`、`tool-call-*` / `tool-input-*`、`tool-output-available`、`context-update`、`stats-update`、`[DONE]`。`run.finished` 为唯一终态事件（载荷含 status / finish_reason / usage / model_calls），`finish` / `abort` / `error` 编码名退役。durable 业务事件 SHALL 携带 `run_id` 与 sequence；transient 事件 SHALL 带 transient 标记且不占 sequence；keepalive 注释帧 SHALL 仅由传输层注入。该事件词汇对主会话 run 与子 Agent run 为同一套。

#### Scenario: 创建后独立订阅
- **WHEN** 已认证用户成功创建 run
- **THEN** 创建响应 SHALL 返回 `run_id` 与 `assistant_message_id`
- **AND** 客户端 SHALL 能使用独立 SSE 端点订阅该 run

#### Scenario: 文本增量兼容
- **WHEN** run 产生文本增量且客户端订阅 SSE
- **THEN** 客户端 SHALL 收到兼容的 `text-delta` 帧

### Requirement: SSE 传输稳定性

流式路径 SHALL 配置合理的代理/应用超时；服务端 MAY 按可配置间隔发送 SSE 注释保活帧。连接类写入失败 SHALL 可观测，并 SHALL 只关闭对应 subscription，不得笼统降级为 run 业务错误或取消 producer。

客户端 SHALL 检查 durable 业务事件 sequence；transient 事件 SHALL 直接应用、不参与 sequence 记账。发现 sequence gap、网络异常或未收到终态的 EOF 时，客户端 SHALL 查询权威 run 状态并重新订阅，SHALL NOT 把该 EOF 当作成功完成。客户端 SHALL 对流读取实施读超时：超过阈值未收到任何字节（含 keepalive）SHALL 视为半开连接并主动断开进入恢复流程，SHALL NOT 无限期等待。

#### Scenario: 保活不污染总线
- **WHEN** SseDelivery 注入 keepalive
- **THEN** PersistSink / ChannelDelivery SHALL NOT 将其当作 RunEvent 业务事件

#### Scenario: 无终态 EOF 触发恢复
- **WHEN** 浏览器流在未收到终态事件时结束
- **THEN** chat 页 SHALL 保持 run 未完成语义并查询/重订阅
- **AND** SHALL NOT 调用成功收尾回调

#### Scenario: 半开连接读超时

- **WHEN** TCP 连接半开导致流读取在超时阈值内无任何字节到达
- **THEN** 客户端 SHALL 主动取消读取并进入权威快照恢复流程
- **AND** SHALL NOT 永久停留在等待读取状态

### Requirement: 客户端 SHALL 以 snapshot replace 和 sequence 连续性恢复

客户端收到 run-snapshot SHALL replace 相同 assistant 的 parts，并设置 last_sequence。durable 业务 sequence 小于等于 last_sequence SHALL 忽略；等于 last_sequence+1 SHALL apply；大于 last_sequence+1 SHALL 停止 reader并进行 snapshot recovery。transient 事件 SHALL 不经 sequence 判定直接 apply。无终态 EOF SHALL NOT 触发成功或失败终态回调。

#### Scenario: sequence gap 不继续渲染

- **WHEN** last_sequence=20 而下一 durable 事件 sequence=23
- **THEN** 客户端 SHALL 丢弃该事件并进入 snapshot recovery

#### Scenario: transient 事件不触发 gap

- **WHEN** last_sequence=20 时先到达一条 transient text-delta，随后 sequence=21 的 durable 事件
- **THEN** transient 事件 SHALL 正常应用
- **AND** sequence=21 的事件 SHALL 正常 apply，SHALL NOT 被误判为 gap

## ADDED Requirements

### Requirement: run 流客户端 SHALL 为单一传输实现

chat 页的 run 流消费、会话信令流与子会话详情视图的流消费 SHALL 由同一传输客户端实现提供（SSE 帧解析含 `[DONE]`/CRLF、读超时、退避重连、sequence 记账、终态判定、abort/代际隔离、断流后权威快照收口）；各视图仅保留各自的领域事件分派，SHALL NOT 各自维护一套流传输代码。`[DONE]` SHALL 被协议层正确处理，SHALL NOT 产生解析错误日志。

#### Scenario: 子会话视图具备读超时保护

- **WHEN** 子会话详情视图订阅的流出现半开连接
- **THEN** 传输客户端 SHALL 在读超时后进入恢复流程
- **AND** SHALL NOT 永久挂起等待

#### Scenario: DONE 不产生解析噪音

- **WHEN** 任一流以 `[DONE]` 正常结束
- **THEN** 传输客户端 SHALL 将其识别为流结束标记
- **AND** SHALL NOT 在控制台产生解析失败警告

#### Scenario: 传输语义单实现

- **WHEN** 代码审查发现主聊天与子会话的流重连/超时/sequence 逻辑
- **THEN** 其 SHALL 委托同一传输客户端
- **AND** SHALL NOT 存在第二套 SSE 解析或重连实现

### Requirement: 子会话视图 SHALL 复用主聊天的投影函数与宿主壳组件

子会话详情视图的 assistant 流式投影 SHALL 使用与主聊天相同的投影函数族（text/reasoning/tool 增量追加、工具输出应用）；消息行宿主壳——run-meta 行（轮数/步数/耗时与折叠）、「本轮未完成」blocker、统计条、stop/send 单按钮、composer 工具栏、HITL 审批卡、来源面板——SHALL 为共享组件，两视图 SHALL NOT 各持一份同构实现。差异行为（如乐观停止 vs 等待往返）SHALL 经组件参数表达。

#### Scenario: 子会话流式追加与主聊天同实现

- **WHEN** 子会话详情视图处理 text-delta / reasoning-delta 帧
- **THEN** 投影 SHALL 由主聊天同一投影函数族完成
- **AND** redacted-thinking 缓冲等富语义 SHALL 在两侧一致生效

#### Scenario: 宿主壳组件单一来源

- **WHEN** 修改统计条或停止按钮的视觉与交互
- **THEN** 修改 SHALL 只发生在一处共享组件
- **AND** 主聊天与子会话视图 SHALL 同步获得更新
