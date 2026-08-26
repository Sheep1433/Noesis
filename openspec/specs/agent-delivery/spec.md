# agent-delivery Specification

## Purpose

本能力规定一次 Agent run 的 **Delivery Fan-out 与 Run 生命周期**：内部 typed `RunEvent`、RunHandle 单写入边界、单调 sequence 与原子 snapshot、Run 身份与显式状态机、幂等创建与冲突防止、PersistWriter 落库与重启恢复、模型重试 attempt 隔离与工具取消语义、RunManager 资源上限、SseDelivery（浏览器 SSE）、ChannelAdapter SPI 与绑定、以及 Telegram / 飞书通道运行时。配置/密钥/设置 UI 属于用户设置面（见 `user-settings`）；本能力只消费已持久化配置。代码锚点：`backend/packages/noesis-core/src/noesis/chat/delivery/`、`backend/packages/noesis-core/src/noesis/chat/runs/`、`backend/packages/noesis-core/src/noesis/services/run_service.py`、`backend/packages/noesis-core/src/noesis/services/channel_run_service.py`。

## Requirements

### Requirement: RunEvent 为内部事件语言

系统 SHALL 定义结构化 RunEvent，至少覆盖：run 开始、文本/推理增量与结束、工具输入/输出、用量/上下文、HITL 请求与暂停（`hitl_pending`）、完成/中止/错误。

执行层 **SHALL NOT** 将 SSE 文本帧作为唯一权威内部表示。

#### Scenario: HITL 进入总线

- **WHEN** LangGraph 产生 HITL interrupt
- **THEN** 总线 SHALL 发布 HitlRequired（或等价），且可继以 RunPaused(reason=hitl_pending)；**SHALL NOT** 建模为 RunCompleted

### Requirement: RunHandle SHALL 支持多 Delivery

对每个 `run_id`，RunHandle SHALL 支持一个或多个 SseDelivery、ChannelDelivery 并发订阅。PersistWriter SHALL 使用独立 latest-wins 单槽，不得作为可因 queue overflow 注销的普通 subscriber。producer SHALL 由 run 生命周期拥有，任一 Delivery 取消订阅或写失败 SHALL NOT 取消 producer、PersistWriter 或其它 Delivery。只有明确的用户停止、系统 run timeout、持久化阻塞、HITL 终态决策、服务 shutdown 或授权管理操作 MAY 取消 producer。keepalive SHALL 仅由 SseDelivery 注入，SHALL NOT 广播为业务 RunEvent，也 SHALL NOT 推进 run sequence。

#### Scenario: PersistWriter 与多个 SSE 同时工作

- **WHEN** 同一 run 启用独立 PersistWriter 且两个浏览器 SseDelivery 已订阅
- **THEN** 两个 SseDelivery SHALL 在 terminal transaction 提交后观察到相同完成事件
- **AND** PersistWriter SHALL 已先提交权威 terminal

#### Scenario: 单个 SSE 断开不影响其它订阅

- **WHEN** 两个 SseDelivery 中一个断开
- **THEN** producer、PersistWriter 与另一个 SseDelivery SHALL 继续工作

#### Scenario: 无浏览器订阅仍完成

- **WHEN** run 创建后所有浏览器订阅均断开但 Agent 正常完成
- **THEN** run SHALL 进入 `completed`
- **AND** assistant SHALL 正常终态落库

### Requirement: RunEvent SHALL 具有单调 sequence

除传输 keepalive 外，每个会影响 run 快照或客户端显示的 RunEvent SHALL 在该 run 内获得严格递增的 `sequence`。客户端与服务端 SHALL 使用 sequence 去重和检测缺口；不同 run 的 sequence 无需可比较。

#### Scenario: sequence 严格递增

- **WHEN** 同一 run 依次产生 text delta、tool start 与 tool result
- **THEN** 后一事件的 sequence SHALL 大于前一事件

#### Scenario: keepalive 不推进 sequence

- **WHEN** SSE Delivery 在无业务事件期间发送 keepalive
- **THEN** run 的 last_sequence SHALL 保持不变

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
- **AND** SHALL 保留最多一个 immutable terminal candidate 低频重试
- **AND** SHALL NOT 在 terminal transaction 提交前发布 error、partial 或 completed

### Requirement: 每个 run SHALL 使用稳定身份与显式状态机

系统 SHALL 为每次逻辑 Agent 执行分配稳定 `run_id`，并关联唯一 `user_id`、`session_id`、`assistant_message_id`、`qa_type` 与 `origin`。同一 run 在 HITL resume、客户端重订阅和 Delivery 变化时 SHALL 保持上述身份不变。

run 状态 SHALL 至少包含：`queued`、`running`、`retrying`、`hitl_pending`、`completed`、`partial`、`error`、`interrupted`。`completed`、`partial`、`error`、`interrupted` 为互斥终态；系统 SHALL 只接受第一个合法终态转换。

#### Scenario: HITL resume 保持 run 身份

- **WHEN** run 进入 `hitl_pending` 后用户提交合法审批
- **THEN** 系统 SHALL 使用原 `run_id` 与 `assistant_message_id` 恢复到 `running`
- **AND** SHALL NOT 新建第二条 assistant 消息

#### Scenario: HITL 分段结束不关闭 Run 订阅

- **WHEN** Agent 因 HITL 进入 `hitl_pending` 且上游执行分段产生 `[DONE]`
- **THEN** 系统 SHALL NOT 将该分段结束标记投递为整个 Run 的终止事件
- **AND** 原 subscriber SHALL 能在审批后继续接收同一 `run_id` 的事件

#### Scenario: 审批时原浏览器订阅已经退出

- **WHEN** 用户提交合法审批时浏览器已没有活跃的 Run subscriber
- **THEN** 客户端 SHALL 在恢复请求成功后重新订阅原 `run_id`
- **AND** SHALL 从权威 snapshot sequence 继续消费而不重复创建 Run

#### Scenario: 终态不可覆盖

- **WHEN** run 已进入 `completed` 后又收到延迟到达的 cancel 或 error
- **THEN** 系统 SHALL 保持 `completed`
- **AND** SHALL 记录可定位的忽略日志

### Requirement: 系统 SHALL 提供原子 snapshot 与后续订阅

系统 SHALL 在同一 run 临界区内完成 subscriber 注册、当前 parts 快照复制与 `snapshot_sequence` 读取，使 subscriber 收到快照后能够连续消费所有 `sequence > snapshot_sequence` 的事件。

当 `after_sequence` 可由有界事件缓存连续补齐时，系统 MAY 补发缺失事件；无法补齐时 SHALL 返回权威 `run-snapshot`，客户端 SHALL 以 replace 语义重建当前 assistant。

#### Scenario: snapshot 与订阅之间不丢事件

- **WHEN** 新 subscriber 加入时 producer 并发产生文本事件
- **THEN** 该文本 SHALL 出现在 snapshot 或后续事件中且仅出现一次有效投影

#### Scenario: 事件缓存不足时重同步

- **WHEN** 客户端请求的 `after_sequence` 早于服务端可补发窗口
- **THEN** 服务端 SHALL 返回包含当前 parts 与 `snapshot_sequence` 的权威 snapshot
- **AND** SHALL NOT 假装已连续补齐旧事件

### Requirement: run 元数据与节流检查点 SHALL 持久化

系统 SHALL 在 PostgreSQL 持久化 run 身份、状态、last_sequence、终态原因、重试元数据、owner 与时间戳。assistant 实时快照 SHALL 保持单行身份；系统 MAY 在完整工具结束、HITL pending、阶段结束等语义边界或可配置节流条件满足时更新同一 assistant 的 parts 检查点，但 SHALL NOT 按 token 更新数据库。终态持久化 SHALL 使用 compare-and-set 或等价互斥机制，防止完成、停止、超时和断开处理互相覆盖。

#### Scenario: 高频文本不逐 token 写库

- **WHEN** 模型连续产生多个 text delta 且尚未达到检查点条件
- **THEN** 系统 SHALL 在内存更新快照
- **AND** SHALL NOT 为每个 delta UPDATE assistant 正文

#### Scenario: 工具结束形成检查点

- **WHEN** 一个可能长时间运行的工具完成并形成完整 tool part
- **THEN** PersistSink SHALL 在节流与终态互斥规则下持久化该语义检查点

### Requirement: run 创建 SHALL 幂等且保持事务原子性

新客户端创建 run 时 SHALL 提交稳定 `client_request_id` 或等价幂等键。系统 SHALL 对用户与幂等键建立唯一约束，并在同一数据库事务中创建 user message、assistant skeleton 与 queued run；producer SHALL 仅在事务提交成功后启动。

相同用户使用相同幂等键和相同请求摘要重试时，系统 SHALL 返回原 `run_id` 与消息身份，不得重复插入消息或启动 producer。幂等键相同但请求摘要不同时 SHALL 返回 409。

#### Scenario: 创建成功但响应丢失

- **WHEN** 首次创建已提交但客户端未收到响应，并使用相同幂等键重试
- **THEN** 系统 SHALL 返回首次创建的 `run_id`
- **AND** SHALL 仅存在一条对应 user message、assistant message 和一个 producer

#### Scenario: 持久化事务失败

- **WHEN** 创建 user message、assistant skeleton 或 agent_run 中任一步失败
- **THEN** 整个事务 SHALL 回滚
- **AND** SHALL NOT 启动 producer

### Requirement: 同一 session 的非终态 run SHALL 防止冲突执行

P0 系统 SHALL 默认限制同一 session 同时最多存在一个非终态交互 run。新建冲突请求 SHALL 返回 HTTP 409 或可识别的等价冲突，并返回当前 active run 身份供客户端加入；系统 SHALL NOT 静默创建第二个共享 LangGraph thread 的并发 run。

#### Scenario: 重复发送返回 active run

- **WHEN** session 已有 `running` run 且用户再次创建普通交互 run
- **THEN** 系统 SHALL 返回冲突及 active `run_id`
- **AND** SHALL NOT 启动第二个 producer

### Requirement: PersistWriter SHALL 独占流式 assistant 落库

RunService SHALL 负责骨架插入；PersistWriter SHALL 负责节流语义检查点，terminal handler SHALL 负责 run + assistant 同事务终态（completed / error / partial / interrupted），并遵循同一 assistant 身份与终态互斥。检查点 MAY 更新完整 parts 快照，但 SHALL NOT 按 token 更新正文。落库 SHALL NOT 依赖浏览器 SSE 存活。消息/run 元数据 SHALL 记录 `origin`（如 `web`、`telegram`、`feishu`、`cron`、`eval`）。

`hitl_pending` 时 SHALL 保持 assistant `streaming`；仅真实终态事件落库。resume SHALL 使用同一 `run_id` 与 `assistant_message_id`。

#### Scenario: 无 SSE 仍终态

- **WHEN** 没有 SSE subscriber 的 run 完成
- **THEN** assistant SHALL 为 completed（或等价成功态）

#### Scenario: 用户停止经统一生命周期

- **WHEN** 用户触发停止且 RunLifecycle 原因为用户停止
- **THEN** terminal handler SHALL 先将 assistant 更新为 partial 并带上与现网一致的停止语义，且其它 Delivery 随后 SHALL 收到同一终态

### Requirement: 后端重启 SHALL 安全收口悬空 run

应用启动后 SHALL 识别无活跃执行 owner 的非终态 run，并将其收口为 `interrupted`；对应 assistant SHALL 进入 `partial` 或现有等价非成功终态，`finish_reason=server_restart`。系统 SHALL 保留最近检查点，并将未完成工具标记为结果未知或错误。

系统 SHALL NOT 因恢复悬空 run 自动重新调用模型、工具、子 Agent 或外部平台操作。仅当 HITL pending 的 checkpoint、pending token、用户和消息身份均可验证时，系统 MAY 保持 `hitl_pending`。

#### Scenario: 重启不重放工具

- **WHEN** 后端重启时发现旧 run 停留在工具 running 状态
- **THEN** 系统 SHALL 将 run 标为 `interrupted`
- **AND** SHALL NOT 自动再次调用该工具

#### Scenario: 悬空文本 run 可见部分结果

- **WHEN** 后端重启时 run 已有持久化 parts 检查点
- **THEN** 历史消息 SHALL 保留该检查点内容
- **AND** 用户 SHALL 能看到服务重启导致中断的明确原因

### Requirement: 用户停止 SHALL 按 run 身份鉴权并幂等

停止操作 SHALL 以 `run_id` 定位目标，并验证 run 属于当前用户与 session。重复停止终态 run SHALL 返回幂等结果，不得影响同 session 的其它历史 run。

#### Scenario: 停止当前 run

- **WHEN** run 所有者对仍在运行的 run 发起 stop
- **THEN** producer SHALL 被取消
- **AND** run 与 assistant SHALL 进入 `partial`，`finish_reason=stopped`

#### Scenario: 越权停止被拒绝

- **WHEN** 用户尝试停止其他用户的 run
- **THEN** 系统 SHALL 拒绝请求
- **AND** 目标 run SHALL 不受影响

### Requirement: 模型重试 SHALL 按 attempt 隔离投影

每次模型调用 SHALL 使用 run 内可区分的 `attempt_id`，相关正文、reasoning 与工具提议事件 SHALL 能关联该 attempt。不同 attempt 的增量 SHALL NOT 无条件追加为同一正文；已废弃 attempt 的迟到事件 SHALL 被忽略并记录。

系统 MAY 在尚未产生用户可见正文且未开始工具时自动重试。已产生正文时，只有服务端能够从权威 snapshot 撤销该 attempt 的未确认片段才 MAY 替换后重试；否则 SHALL 收口为 partial/error。任何工具、HITL 或子 Agent 已开始后，系统 SHALL NOT 自动重试整个模型步骤。

#### Scenario: 首个 attempt 无输出即断开

- **WHEN** 模型连接在产生正文和工具调用前断开且错误可重试
- **THEN** 系统 MAY 创建新 attempt 自动重试
- **AND** SHALL 发出可感知的 retrying 状态

#### Scenario: 工具开始后模型连接失败

- **WHEN** 当前 attempt 已开始工具调用后发生模型连接失败
- **THEN** 系统 SHALL NOT 自动重新执行整个模型步骤
- **AND** SHALL NOT 重复调用该工具

#### Scenario: 旧 attempt 迟到事件

- **WHEN** 已切换到新 attempt 后收到旧 attempt 的 text delta
- **THEN** 系统 SHALL 忽略该事件
- **AND** 当前 snapshot SHALL 不出现重复正文

### Requirement: 工具取消 SHALL 区分请求、确认与未知结果

工具适配层 SHALL 为工具调用记录 `tool_call_id`，应用可配置 timeout 与 cancel grace period，并在能力允许时清理本地子进程、HTTP 或 MCP 请求。用户停止不得被解释为外部副作用一定撤销。

取消或超时后到达的结果 SHALL NOT 覆盖 run 已确定终态。无法确认外部操作是否执行时，tool part SHALL 记录 `outcome=unknown` 与安全用户文案；高风险且不具备幂等或状态查询能力的工具 SHALL NOT 自动重试。

#### Scenario: 远程工具取消结果未知

- **WHEN** 用户停止 run，但远程工具在 grace period 内未确认取消
- **THEN** run MAY 收口为 partial/stopped
- **AND** 工具结果 SHALL 标记 unknown，不得标记已撤销或成功

#### Scenario: 取消后的迟到结果

- **WHEN** run 已因用户停止进入终态后收到工具成功结果
- **THEN** 系统 SHALL 丢弃该迟到结果对消息投影的修改
- **AND** SHALL 记录可定位日志

### Requirement: RunManager SHALL 实施资源上限与确定回收

系统 SHALL 对 active run、单 run 运行时长、event buffer 事件数与字节数、subscriber 队列、输出长度、HITL pending 时长和 shutdown drain 设置可配置上限。超过用户级并发限制时 SHALL 拒绝创建；运行中超过限制时 SHALL 使用稳定错误码收口，不得无限占用内存。

run 终态可靠落库且超过 terminal 内存保留期后，RunManager SHALL 释放 producer task、builder、event buffer、subscriber 与 terminal future。后续查询 SHALL 从 PostgreSQL 权威 snapshot 返回结果。

#### Scenario: terminal run 被回收

- **WHEN** run 已终态落库且超过内存保留期
- **THEN** RunManager SHALL 释放该 run 的内存对象
- **AND** GET run SHALL 仍返回持久化终态与 snapshot

#### Scenario: HITL 长期无人处理

- **WHEN** hitl_pending 超过配置的有效期
- **THEN** 系统 SHALL 按既有 HITL timeout 策略收口
- **AND** 重复或过期审批 SHALL 不恢复第二个 producer

### Requirement: P0 部署 SHALL 保持 live owner 可达并提供关联观测

在未实现跨实例 owner claim 与事件总线前，生产部署 SHALL 使用单 active backend，或保证 run 的创建、查询、订阅和停止请求返回拥有该 RunHandle 的实例。系统 SHALL NOT 在 owner 不可达时宣称能够继续 live run；只能返回持久化 snapshot 或明确的暂不可恢复状态。

日志与指标 SHALL 能以 `run_id` 关联模型 attempt、tool call 与 delivery，并至少观测 active run 数量、队列事件/字节、overflow、检查点失败、取消延迟、客户端重连与 terminal 回收。

#### Scenario: 请求未到达 live owner

- **WHEN** active run 的订阅或停止请求到达不持有该 RunHandle 的实例
- **THEN** 系统 SHALL 返回持久化状态或明确的 owner 不可达结果
- **AND** SHALL NOT 创建第二个 producer 或伪报 run 已完成

#### Scenario: Delivery 故障可独立定位

- **WHEN** Agent 已完成但某个平台投递失败
- **THEN** 运维记录 SHALL 能通过 run_id 定位独立 delivery_id 与失败原因
- **AND** run 终态 SHALL 保持 completed

### Requirement: Run 状态写入 SHALL 保证 sequence 与 projection 原子一致

系统 SHALL 使每个 Run 的 sequence 分配、projection reduce、status 变更、replay buffer 写入、subscriber 注册和 snapshot 复制通过同一 RunHandle lock 完成。`snapshot_sequence=N` SHALL 精确对应 apply 到 N 的 projection，不得预先包含 N+1。

#### Scenario: apply 与 subscribe 并发

- **WHEN** 事件 N 正在 apply 时另一个 subscriber 加入
- **THEN** subscriber SHALL 在 snapshot 或后续 tail 中有效观察到事件 N 一次
- **AND** SHALL NOT 丢失或重复 apply 事件 N

### Requirement: Runtime raw event SHALL 只经过一条 typed 主路径

`COMMON_QA`、`FAULT_OPERATION_QA` 与 `SUPER_AGENT_QA` 的 raw event SHALL 由唯一 RuntimeEventMapper 映射为 typed RunEvent，再交给 RunHandle 和 Delivery。SSE 字符串 SHALL 只在 SseDelivery 边界编码；主路径 SHALL NOT 保留内部 SSE encode/parse 往返、Web 专用重复 EventBus 或 active registry。

#### Scenario: 未知 raw event 不污染投影

- **WHEN** RuntimeEventMapper 收到未支持的 raw event
- **THEN** 系统 SHALL 记录可定位信息并丢弃
- **AND** SHALL NOT 分配 sequence 或修改 projection

### Requirement: PersistWriter SHALL 使用 immutable checkpoint 与 latest-wins 合并

CheckpointRequest SHALL 携带与 `snapshot_sequence` 绑定的 immutable snapshot。每 Run待写单槽 SHALL 只保留 sequence 最大的 checkpoint；repository SHALL 以 stored sequence guard 防止回退，并在同一 transaction 更新 run snapshot/metadata 与 assistant content。

#### Scenario: 高频 checkpoint 合并

- **WHEN** writer 正在写 N 时收到 N+1 至 N+20
- **THEN** pending SHALL 最多保留一份
- **AND** 最终待写请求 SHALL 为已收到的最大 sequence

#### Scenario: 迟到 checkpoint 不回退数据库

- **WHEN** 数据库已存 sequence 50，随后收到 sequence 45
- **THEN** run 与 assistant SHALL 均保持 sequence 50 对应内容

### Requirement: 权威 terminal SHALL 先持久化再投递

completed、partial、error、interrupted SHALL 使用 run 与 assistant 同事务 compare-and-set，并同时写 final snapshot 与 `last_sequence`。只有 transaction committed 后，系统才 SHALL 切换 live projection、写 replay、fan-out terminal 并发送 `[DONE]`。

#### Scenario: terminal CAS 未赢

- **WHEN** 其它合法路径已终态化该 Run
- **THEN** 当前路径 SHALL 采用数据库权威 snapshot
- **AND** SHALL NOT 发布当前 candidate 的冲突 terminal

#### Scenario: terminal 持久化持续失败

- **WHEN** terminal transaction 在同步 budget 内持续失败
- **THEN** producer SHALL 停止且只保留一个 immutable candidate 低频重试
- **AND** Delivery SHALL NOT 收到伪 terminal

### Requirement: 迟到 producer 与 model attempt 事件 SHALL 被拒绝

每个初始/HITL resume producer segment SHALL 使用递增的进程内 generation；每个新模型尝试 SHALL 使用递增 attempt_id。不匹配事件 SHALL 在 projection reduce 前被拒绝，不分配 sequence、不进入 replay 或 snapshot。generation SHALL NOT 持久化或进入 SSE payload。

#### Scenario: HITL resume 后旧 producer 迟到

- **WHEN** 新 generation 已启动后旧 task 发出 delta
- **THEN** delta SHALL 被丢弃并增加 stale generation 指标

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

系统 SHALL 定义 ChannelAdapter（`channel_type`、capabilities、入站规范化、出站投影）与 ChannelRegistry。`channel_type` SHALL 至少支持 `telegram` 与 `feishu`（`wechat` MAY 预留）。已启用的通讯通道在运行时 SHALL 以 ChannelAdapter 注册到 ChannelRegistry，入站经 Session/Binding 路由到同一 Agent run 入口，出站订阅 RunEvent 总线。通道实现 SHALL NOT 复制一套独立于消息 SSOT 的 transcript，也 SHALL NOT 将浏览器 SSE 连接作为出站前提。

ChannelBinding SHALL 持久化 `(user_id, channel_type, external_chat_id[, thread_id]) → session_id`。未配对发送方 **SHALL NOT** 触发任意用户的特权 Agent 执行。

#### Scenario: 未配对拒绝

- **WHEN** 未绑定账号向已启用通道发消息
- **THEN** 系统 SHALL 拒绝执行 Agent（可回复配对引导）

#### Scenario: 配置启用后可解析 adapter

- **WHEN** 用户启用 `type=telegram`（或 `feishu`）通道且运行时已注册对应 adapter
- **THEN** ChannelRegistry 按 `channel_type` 解析成功，且入站/出站路径不经过 `LangGraphSseBridge` 字符串 yield

### Requirement: 通道消息写入 SSOT

通道入站用户文本 SHALL 写入与 Web 相同的消息存储；出站 **SHALL NOT** 要求同 session 存在浏览器 SSE。

#### Scenario: 网页可见通道消息

- **WHEN** 已配对用户经通道发送文本且落库成功
- **THEN** 该 `session_id` 的 messages API SHALL 包含对应用户消息

#### Scenario: 仅通道在线

- **WHEN** run 由通道触发且无浏览器 SSE
- **THEN** 用户仍 SHALL 能在通道收到终态（或 capabilities 允许的流式）回复

### Requirement: 通道配置归属 settings；运行时消费配置

通道 CRUD、密钥、配对与设置 UI **SHALL NOT** 在 Delivery 内另建一套（见 `user-settings`）；运行时 SHALL 读取已持久化配置。adapter 在启用时 SHALL 可真收发（测试可用 stub 替换）。

#### Scenario: 启用后可解析

- **WHEN** 用户启用某通道且 pairing 有效
- **THEN** Registry SHALL 解析到可执行 adapter，入站/出站不经 SSE 字符串 yield

### Requirement: 出站尊重 capabilities

不支持 `streaming_edit` 的通道 SHALL 在 run 完成后投递终态文本；默认避免把完整工具细节镜像到 IM（除非 adapter 显式启用）。

#### Scenario: 无 edit 则终态

- **WHEN** adapter 声明 `streaming_edit=false` 且 run 完成
- **THEN** 通道内容 SHALL 基于终态文本投影

### Requirement: Delivery 失败 SHALL 与 Agent run 终态隔离

SseDelivery 或 ChannelDelivery 的出站失败 SHALL 只影响该 Delivery，并记录可定位的 delivery 状态/日志。平台投递失败 SHALL NOT 把已经成功的 Agent run 改写为 error，也 SHALL NOT 阻止 PersistSink 完成终态落库。

#### Scenario: 通道发送失败不污染 run

- **WHEN** Agent 已完成但通道出站调用失败
- **THEN** run 与 assistant SHALL 保持 completed
- **AND** delivery SHALL 记录独立失败结果

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

**Telegram 运行时**

### Requirement: Telegram 运行时 SHALL 在开关启用时 long-poll 入站

当配置 `messaging.telegram_runtime_enabled=true` 时，系统 SHALL 对每个用户已启用且含 bot token 的 `type=telegram` 通道启动 long-poll（`getUpdates`）。开关关闭时 **SHALL NOT** 发起 Bot API 轮询。

#### Scenario: 开关关闭不轮询

- **WHEN** `telegram_runtime_enabled=false`
- **THEN** 系统 SHALL 不调用 Telegram `getUpdates`

### Requirement: Telegram 入站 SHALL 经配对后写入 SSOT 并 headless 跑 Agent

已配对入站文本 SHALL：解析 ChannelBinding → `get_or_create_session` → 写入 user 消息（含 `origin=telegram` 与外部 message id）→ 无浏览器 SSE 的 headless Agent 跑次 → PersistSink 终态落库。未配对 SHALL 拒绝跑 Agent，并可回复配对引导。

#### Scenario: 已配对跑通

- **WHEN** 配对 chat 发送文本且运行时启用
- **THEN** 对应 session 的 messages SHALL 含该 user 消息与终态 assistant 行

### Requirement: Telegram 出站 SHALL 支持伪流式文本与工具进度且不依赖浏览器 SSE

系统 SHALL 经 `sendMessage` + 节流 `editMessageText` 向该 chat 投影 assistant 文本（可带光标预览，终态去掉光标）。工具开始时 SHALL 使用**独立**进度消息展示工具名/短 preview（可 accumulate 更新）；**SHALL NOT** 将完整 tool output 发到 Telegram。**SHALL NOT** 要求存在浏览器 SSE 连接。

#### Scenario: 无网页在线仍投递

- **WHEN** headless run 成功完成且无 SSE 订阅者
- **THEN** 用户仍 SHALL 在 Telegram 收到回复（含流式过程中的 edit 与终态）

#### Scenario: 工具进度不镜像结果

- **WHEN** Agent 调用工具并返回大段 output
- **THEN** Telegram 进度气泡 SHALL 仅含工具名与短 preview，SHALL NOT 含完整 tool output

### Requirement: Telegram HITL 审批 SHALL 对齐网页 approve/reject

当 headless run 以 `hitl_pending` 结束且 kind 为审批时，系统 SHALL 向该 chat 发送含工具摘要的审批卡片，并附 Inline Keyboard（批准 / 拒绝；网络类 execute 可附「本会话放行」）。用户点击 SHALL 调用与网页相同的 decisions / grant_scope resume 路径继续同一 `assistant_message_id`，**SHALL NOT** 要求打开浏览器。clarification（ask_user）SHALL 接受下一条文字消息作为 respond。

#### Scenario: 批准后继续

- **WHEN** 用户点击「批准」且 pending HITL 仍有效
- **THEN** 系统 SHALL resume SuperAgent 并继续向 Telegram 投影后续输出

#### Scenario: 拒绝

- **WHEN** 用户点击「拒绝」
- **THEN** 系统 SHALL 以 reject decision resume，并移除键盘

**飞书运行时**

### Requirement: 飞书运行时 SHALL 使用企业自建应用长连接接收入站事件

系统 SHALL 在 `messaging.feishu_runtime_enabled=true` 且部署级飞书应用凭据完整时启动一个官方 SDK WebSocket 客户端；同一进程内所有已启用飞书用户绑定 SHALL 共享该应用连接。关闭开关后 SHALL 停止处理入站事件，且 SHALL NOT 要求公网入站 URL。`channel_type=feishu` SHALL 注册可执行 Adapter，而非 Stub 或仅可保存的配置类型。

#### Scenario: 开关关闭不建立连接

- **WHEN** `messaging.feishu_runtime_enabled=false`
- **THEN** 系统 SHALL NOT 启动飞书 WebSocket 客户端

#### Scenario: 多个用户共享应用连接

- **WHEN** 两个 Noesis 用户分别绑定不同的飞书 Open ID
- **THEN** 系统 SHALL 通过同一个飞书应用连接接收两人的消息
- **AND** SHALL 按 Open ID 将消息路由到各自的 Noesis 用户、session 与 Agent 配置

### Requirement: 飞书事件 SHALL 快速确认并异步执行

消息与卡片事件 handler SHALL 在平台确认时限内完成校验和入队，SHALL NOT 在 handler 内等待 LLM、工具或完整 Agent run；异步任务失败 SHALL 记录到通道健康状态。

#### Scenario: Agent 执行耗时超过确认窗口

- **WHEN** 一个合法消息触发耗时 Agent run
- **THEN** 飞书事件 handler SHALL 先成功返回
- **AND** Agent SHALL 在后台继续执行并投递结果

### Requirement: 飞书文本入站 SHALL 执行配对、群聊与幂等策略

系统 SHALL 以发送者 `open_id` 进行用户配对；群聊 chat_id 或 message_id SHALL 只作为会话线程与回复目标，SHALL NOT 单独授予群内所有成员调用 Agent 的权限。单聊文本可直接触发，群聊文本只有明确 @机器人时才可触发并 SHALL 去除机器人 mention。系统 SHALL 以 `event_id` 或 `message_id` 去重，未配对、重复或不受支持的消息 SHALL NOT 触发 Agent。

#### Scenario: 已配对单聊触发 Agent

- **WHEN** 已配对 open_id 向机器人发送文本
- **THEN** 系统 SHALL 将文本写入绑定 session 的消息 SSOT 并启动 headless run

#### Scenario: 群聊没有提及机器人

- **WHEN** 群成员发送未 @机器人的普通文本
- **THEN** 系统 SHALL 忽略该事件且 SHALL NOT 写入用户消息

#### Scenario: 同群未配对成员提及机器人

- **WHEN** 未配对成员在已存在目标 chat_id 的群中 @机器人
- **THEN** 系统 SHALL 拒绝触发 Agent

#### Scenario: 飞书重推同一事件

- **WHEN** 系统再次收到相同 event_id 或 message_id
- **THEN** 系统 SHALL NOT 重复写入消息或启动第二次 run

### Requirement: 飞书出站 SHALL 支持节流更新与终态回落

系统 SHALL 将 Agent 可见文本投影到原会话，流式更新 SHALL 节流；工具内容默认只显示名称与短状态。卡片更新失败或内容超过平台限制时 SHALL 回落为分段终态文本，完整工具 output 与敏感数据 SHALL NOT 发往飞书。飞书入站用户消息与 assistant 结果 SHALL 写入网页使用的同一消息 SSOT 并记录 `origin=feishu`。

#### Scenario: 无浏览器连接仍收到回复

- **WHEN** 飞书消息触发 run 且没有浏览器 SSE subscriber
- **THEN** 用户 SHALL 在飞书收到 Agent 终态回复

#### Scenario: 飞书投递失败

- **WHEN** Agent run 已完成但飞书 API 返回错误
- **THEN** run SHALL 保持原 completed 状态
- **AND** 通道 SHALL 独立记录投递失败与可定位信息

#### Scenario: 网页查看飞书会话

- **WHEN** 已配对用户通过飞书完成一轮对话
- **THEN** 对应 session 的 messages API SHALL 返回该轮 user 与 assistant 消息
- **AND** 消息来源 SHALL 可审计为 `feishu`

### Requirement: 飞书运行时 SHALL 提供健康与连接操作

通道操作服务 SHALL 使用部署级应用凭据校验共享应用、向用户配置的目标发送测试消息，并记录连接状态、最近入站、最近出站和脱敏错误；用户通道 API SHALL NOT 接收或返回 App ID、App Secret 或 access token。

#### Scenario: 测试合法飞书配置

- **WHEN** 已认证用户对自己的飞书通道执行连接测试
- **THEN** 系统 SHALL 使用运行时凭据校验应用并返回脱敏健康摘要
