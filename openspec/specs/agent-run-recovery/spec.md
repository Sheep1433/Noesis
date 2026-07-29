# agent-run-recovery Specification

## Purpose
TBD - created by archiving change durable-agent-run-recovery. Update Purpose after archive.
## Requirements
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

### Requirement: run producer SHALL 独立于 Delivery 生命周期

Agent producer SHALL 由 run 生命周期管理器拥有，浏览器 SSE、PersistSink 与 ChannelDelivery SHALL 作为独立订阅者消费同一 run。任一普通 Delivery 断开、写失败或被释放 SHALL NOT 取消 producer 或其它订阅者。

只有明确的用户停止、系统 run timeout、HITL 终态决策、服务 shutdown 或授权管理操作 MAY 取消 producer。

#### Scenario: 浏览器刷新不终止 run
- **WHEN** 浏览器在 run 为 `running` 时断开 SSE
- **THEN** 系统 SHALL 只释放该 SSE subscription
- **AND** producer、PersistSink 与其它 Delivery SHALL 继续运行

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

系统 SHALL 在 PostgreSQL 持久化 run 身份、状态、last_sequence、终态原因、重试元数据、owner 与时间戳。assistant 实时快照 SHALL 保持单行身份；系统 MAY 在完整工具结束、HITL pending、阶段结束等语义边界或可配置节流条件满足时更新同一 assistant 的 parts 检查点，但 SHALL NOT 按 token 更新数据库。

终态持久化 SHALL 使用 compare-and-set 或等价互斥机制，防止完成、停止、超时和断开处理互相覆盖。

#### Scenario: 高频文本不逐 token 写库
- **WHEN** 模型连续产生多个 text delta 且尚未达到检查点条件
- **THEN** 系统 SHALL 在内存更新快照
- **AND** SHALL NOT 为每个 delta UPDATE assistant 正文

#### Scenario: 工具结束形成检查点
- **WHEN** 一个可能长时间运行的工具完成并形成完整 tool part
- **THEN** PersistSink SHALL 在节流与终态互斥规则下持久化该语义检查点

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

### Requirement: 同一 session 的非终态 run SHALL 防止冲突执行

P0 系统 SHALL 默认限制同一 session 同时最多存在一个非终态交互 run。新建冲突请求 SHALL 返回 HTTP 409 或可识别的等价冲突，并返回当前 active run 身份供客户端加入；系统 SHALL NOT 静默创建第二个共享 LangGraph thread 的并发 run。

#### Scenario: 重复发送返回 active run
- **WHEN** session 已有 `running` run 且用户再次创建普通交互 run
- **THEN** 系统 SHALL 返回冲突及 active `run_id`
- **AND** SHALL NOT 启动第二个 producer

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

