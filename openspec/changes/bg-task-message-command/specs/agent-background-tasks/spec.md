# agent-background-tasks Delta

> 基线：`bg-task-durable-facts` 归档后的主规格（本变更依赖其 pending 行队列事实与命名基线，归档顺序须在其后）。

## MODIFIED Requirements

### Requirement: 追加消息（子会话追加 turn）

`send_message(task_id, message)`（模型侧）与 `POST /api/chat/sessions/{id}/subagent-messages`（用户侧，人 / 模型同路径）SHALL 为同一 child session 追加一条 user message 与一个新的标准 `TAgentRun`（下文统称追加消息），SHALL NOT 以中途注入方式改写当前 turn 的模型输入：

- **write-ahead 持久化（队列事实）**：任何路径追加的消息 SHALL 先落库为 child session 的 pending user message 行（`extra.pending_run` 标记，携带 turn 参数）；pending 行与 `bg_task_deliver` 命令行 SHALL 在同一数据库事务内写入——命令失败则行不存在，孤儿 pending 行在结构上不可能。内容与下达时间随行可审计。
- **受理与消费分离**：受理（归属校验、快速失败、容量执法、事务写行插命令、等待结果）SHALL 在任意实例可用；消费（读行、入执行队列或冷恢复开新 turn）SHALL 仅由 leader 的命令消费者执行。受理端 SHALL NOT 本地执行消费语义，SHALL NOT 依赖执行器实例的存在性。模型工具与用户 HTTP SHALL 走同一命令流程，SHALL NOT 存在绕过命令表的本地直通分支。
- **命令即引用**：`bg_task_deliver` 命令 payload SHALL 仅携带 `(child_session_id, message_id)` 引用，SHALL NOT 携带消息内容或 turn 参数。
- **消费幂等**：消费前 SHALL 校验 pending 行仍处 pending 标记，且按 message_id 在执行镜像队列查重；已采纳、已翻转 dropped 或已在镜像队列中的行 SHALL 空转（命令 no_op），SHALL NOT 重复入队或重复开 turn。
- **认领租约**：claimed 状态的命令 SHALL 有界回收（`claimed_at` 超过租约由补扫重置回 pending；leader 晋升对账 SHALL 将遗留 claimed 行重置回 pending）——leader 在认领与终态标记之间崩溃 SHALL NOT 造成命令永久卡死或 pending 行永久滞留。
- **消费分派**：消费按目标任务状态分派——行已非 pending → no_op；任务非终态但热集 miss（换主窗口）→ no_op 延后（SHALL NOT 翻转行）；任务可续或活跃 → 入执行队列或冷恢复；任务不可续（failed / timed_out / 对账 error）→ 翻转 pending 行 dropped 并将命令标 rejected（摘要注明原因）。已取消任务的遗留队列消息：**受理先于取消**的 SHALL NOT 复活任务（命令 no_op，行保留 pending 待续聊触发）；**受理晚于取消**的（用户主动追问）SHALL 正常冷恢复。
- **容量执法点在受理**：队列上限（10）以该任务的 pending 行计数判定，超限 SHALL 在受理时拒绝（不写行、不插命令）；消费对已受理行 SHALL NOT 复查容量。执行镜像队列 SHALL NOT 因上限静默淘汰已受理条目。
- **投递确认与失败语义**：消费采纳 SHALL 复用 launch 事务（清 `pending_run`、盖 `run_id`），SHALL NOT 以独立更新留出「已执行却停在 pending」的崩溃窗口。投递失败 SHALL NOT 终态化任务：冷恢复路径失败时任务 SHALL 回退先前终态（恢复完整先前收口态，SHALL NOT 留下悬空的回收资格判定），链式路径失败时任务 SHALL 保持当前状态，消息行翻转 dropped、调用方收到可诊断错误；冷恢复窗口内受理的停止终态 SHALL 获胜。launch 失败分流：确定性失败 SHALL 直接翻转 dropped，瞬时故障 SHALL 有界重试、耗尽后翻转 dropped——任何失败分支 SHALL NOT 留下滞留 pending 的行。
- **换主时序**：leader 晋升时 SHALL 先完成对账（遗留 run 收口、queued 重建）再启动命令消费——排队任务的追加消息 SHALL 随任务重建保留并消费，SHALL NOT 因消费先于对账被误翻转 dropped。
- **响应契约**：等待窗口内命令 completed SHALL 返回任务投影快照（冷恢复场景含新 run_id）；命令 rejected SHALL 返回 409 与拒绝原因；等待超时 SHALL 返回受理时投影并携带 `command_status: "accepted"` 字段，SHALL NOT 以 500 或无限等待收场——前端由既有轮询与事件流兜底。
- **呈现**：前端 SHALL 对 pending 标记消息呈现待执行状态、对 dropped 标记消息呈现未执行标注，SHALL NOT 将 dropped 消息呈现为已执行的正常用户消息。
- 任务 running：消息排队（FIFO，上限 10）；当前 turn 结束后 executor SHALL 同 thread 链式开新 turn，队列清空前任务保持 running。
- 任务 awaiting_approval：消息入队，审批 resume 完成本 turn 后由同一条链消费。
- 任务 completed / cancelled：SHALL 冷恢复——同 thread 追加消息开新 turn，任务回到 running，结束后更新结果（cancelled 可续为执行/意图分离语义：停止只终止执行，续聊意图保留）。对已从内存热集回收的任务，冷恢复 SHALL 先从 child session descriptor 重建执行条目再开新 turn。
- 任务 failed / timed_out：SHALL 返回错误说明，不可续。
- 每条追加消息的 turn SHALL 支持逐 turn 覆盖执行参数：`model_id`（现有）与 `reasoning_effort`（可选）；用户侧 API 请求体可选 `reasoning_effort` 字段，缺省 SHALL 继承任务创建时的档位，旧客户端不传字段时行为不变。turn 参数在排队期间 SHALL 与消息行绑定持久化，链式开新 turn 时逐条生效。

#### Scenario: follower 受理不再 500

- **WHEN** 多实例部署下，`/subagent-messages` 请求落在非 leader 实例，任务 running
- **THEN** 该实例 SHALL 完成受理（pending 行 + 命令同事务落库）并返回受理结果，SHALL NOT 抛执行器缺失类 500
- **AND** leader 的命令消费者 SHALL 消费该命令，消息进入任务执行队列

#### Scenario: 孤儿 pending 行结构上不可能

- **WHEN** 受理过程中命令插入失败（数据库异常）
- **THEN** pending 行 SHALL 随同一事务回滚，SHALL NOT 出现有行无命令的滞留状态
- **AND** 该任务的追加消息容量 SHALL NOT 被假消息占用

#### Scenario: 消费幂等（重放与中间态空转）

- **WHEN** 同一 `bg_task_deliver` 命令因 leader 换主被重新认领、去重窗口内重复提交，或消费时消息已在执行镜像队列中（行仍 pending 的中间态）
- **THEN** 消费 SHALL 空转（命令 no_op），SHALL NOT 重复入队或重复开 turn

#### Scenario: 认领租约回收

- **WHEN** leader 在命令认领后、终态标记前崩溃，新 leader 晋升
- **THEN** 遗留的 claimed 命令 SHALL 被重置回 pending 并由新 leader 重新消费
- **AND** 对应 pending 行 SHALL NOT 永久滞留（占容量或永久显示待执行）

#### Scenario: 消费拒绝翻转 dropped

- **WHEN** 命令消费时任务已转为不可续终态（failed / timed_out / 对账 error）
- **THEN** 消费者 SHALL 翻转 pending 行 dropped 并将命令标 rejected（摘要注明原因）
- **AND** 该任务的查询投影 SHALL 携带 `undelivered_messages` 计数，SHALL NOT 留下永久 pending 的行

#### Scenario: 已停止任务的遗留消息不被被动复活

- **WHEN** 任务 running 时受理的追加消息尚未消费，用户停止任务（cancelled），随后命令被消费
- **THEN** 消费 SHALL 空转（命令 no_op，摘要注明任务已停止），pending 行保留原状
- **AND** 任务 SHALL NOT 被该消息反向复活开新 turn；用户对该任务的新一轮主动追问 SHALL 正常冷恢复

#### Scenario: 排队任务的指示随任务保留（换主窗口）

- **WHEN** queued 任务的 pending 追加消息遇到 leader 换主，消费先于对账完成的瞬间认领命令
- **THEN** 消费 SHALL 空转延后（任务非终态且热集 miss），SHALL NOT 翻转行 dropped
- **AND** 对账与排队重建完成后，该消息 SHALL 随任务保留并按序消费

#### Scenario: 等待超时降级与拒绝映射

- **WHEN** 命令在等待窗口内未完成，或命令被消费端拒绝
- **THEN** 超时 SHALL 返回受理时投影并携带 `command_status: "accepted"`；rejected SHALL 返回 409 与拒绝原因
- **AND** SHALL NOT 以 500 收场或无限等待；前端由既有轮询与事件流兜底

#### Scenario: 单一路径（工具与 HTTP 同走命令）

- **WHEN** 模型在 leader 的 agent loop 内调用 `send_message` 工具，或用户 HTTP 请求落在任意实例
- **THEN** 两条路径 SHALL 经同一命令流程（受理 → 命令 → 消费），SHALL NOT 存在绕过命令表的本地直通分支

#### Scenario: 镜像队列不静默淘汰

- **WHEN** 容量竞态使受理的追加消息数超过上限（K-1 超限窗口）
- **THEN** 执行镜像队列 SHALL 保留全部已受理条目并依次消费，SHALL NOT 因队列上限静默淘汰任一已受理消息

#### Scenario: 队列满在落库前拒绝

- **WHEN** 某任务 pending 的追加消息已达上限（10）后再追加
- **THEN** SHALL 返回「补话队列已满」类错误，提示等待当前轮完成或合并指示
- **AND** SHALL NOT 产生滞留的 pending 消息行

#### Scenario: 投递失败不终态化任务

- **WHEN** 向 cancelled 任务追加消息触发冷恢复，但 descriptor 解析失败（类型已注销）
- **THEN** 任务 SHALL 回退先前终态（cancelled，保持可续），消息行 SHALL 翻转 dropped
- **AND** 调用方 SHALL 收到含原因的可诊断错误，SHALL NOT 收到任务 failed 终态

#### Scenario: dropped 消息前端呈现

- **WHEN** 子会话抽屉渲染一条被翻转 dropped 的追加消息
- **THEN** 该消息 SHALL 呈现「未执行」标注，SHALL NOT 呈现为已执行的正常用户消息
- **AND** pending 标记的追加消息 SHALL 呈现待执行状态（区别于已被采纳执行的消息）

#### Scenario: 运行中追加指示

- **WHEN** 任务 running 时投递「聚焦中文源」
- **THEN** 当前 turn 结束后子 Agent SHALL 以该消息为新 turn 接续推理（可多轮工具调用）
- **AND** 新 turn 结束前消息 SHALL NOT 消失或重复

#### Scenario: 完成后继续追问

- **WHEN** 向 completed 任务 send_message 追问
- **THEN** 任务 SHALL 回到 running 并开新 turn，结束后结果 SHALL 更新

#### Scenario: 取消任务续聊（执行/意图分离）

- **WHEN** 向 cancelled 任务 send_message（用户停止后又想继续）
- **THEN** 任务 SHALL 回到 running 并同 thread 开新 turn（排队中的追加消息一并消费）

#### Scenario: 失败任务拒绝续话

- **WHEN** 向 failed / timed_out 任务 send_message
- **THEN** SHALL 返回「任务已结束（原因）」类错误说明

#### Scenario: 逐 turn 切换推理档位

- **WHEN** 用户在子会话抽屉选择「高」档位后发送追加消息，任务处于 running
- **THEN** 该消息入队并在成为新 turn 时以「高」档位执行
- **AND** 队列中未指定档位的其他消息 SHALL 按各自绑定参数执行（缺省继承创建时档位）

## ADDED Requirements

### Requirement: 追加消息命名一致性

后台任务的追加消息机制 SHALL 统一使用「追加消息 / message」语义命名（代码符号、文件与目录名、组件名、测试名），SHALL NOT 保留 followup 历史命名、旧名兼容别名或转发层；执行内核、服务、前端与测试对同一机制的称呼 SHALL 为同一词汇。历史叙述（决策记录、已归档 openspec 变更）SHALL NOT 受本条约束。

#### Scenario: 全仓命名门槛

- **WHEN** 对 `backend/packages`、`backend/server`、`backend/tests`、`frontend/src`、`frontend/__tests__` 执行 `grep -ri followup`
- **THEN** SHALL 零命中

#### Scenario: 无兼容别名

- **WHEN** 任一调用方引用追加消息的函数、组件或模块
- **THEN** SHALL 只存在唯一命名（如 `deliver_message` / `send_message` / `MessageQueue`），SHALL NOT 出现旧名转发、弃用标记或双轨入口
