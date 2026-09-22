# agent-background-tasks Specification

## Purpose
本能力规定 SuperAgent 后台子 Agent 任务：子会话身份与进程内执行模型、子 Agent 类型注册表与分发（subagent_type）、`start_task` 单工具同异步（run_in_background）、任务身份进主 Agent graph state、task-worker 编译契约、追加消息续话、子会话详情与事件流、完成通知注入（含来源清单回流）、后台 shell 命令任务、协作式停止与部分成果回收、输出截断终态与子会话用量统计。Run 事件流投递契约见 `agent-delivery`；审批策略见 `agent-hitl`；研究弧来源聚合见 `platform-chat`。
## Requirements
### Requirement: 子 Agent 会话身份

每次 `start_task` SHALL 在父会话下创建一个独立的 child `ChatSession`（`kind=subagent`、`parent_id` 指向父会话、`created_by_tool_call_id` 记录触发调用的模型 tool_call_id），并在同一 launch 事务中写入首条 user message、assistant 骨架与首个标准 `TAgentRun`。launch SHALL 在 child session `extra` 下写入版本化 subagent descriptor（独立键 `subagent`，显式字段 `version`、`type` 与 `model`（该类型解析后的生效模型），读取时校验），供进程重启后重建 worker 的路径按类型与模型取配方。executor 内部 task id 与 child session id SHALL 分离；对外 API 与前端 SHALL 只以 child session id 作为公开身份（目录 `task_id == child_session_id`，`bg-*` 内部 id 不外泄）。左侧会话历史 SHALL 过滤子会话（`parent_id IS NULL`）。父会话软删 SHALL 级联软删 child session 树及其消息，并取消运行中的 run 与后台任务。

#### Scenario: 并行同名独立

- **WHEN** 主 Agent 并行调用两次 `start_task` 且 description 相同
- **THEN** SHALL 创建两个独立 child session / run，`created_by_tool_call_id` 一一对应
- **AND** 父会话卡片 SHALL 各自独立，不按名称合并

#### Scenario: 左侧历史不出现子会话

- **WHEN** 子会话存在且未删除
- **THEN** 会话历史列表 SHALL 不返回该子会话

#### Scenario: 父会话软删级联

- **WHEN** 用户删除父会话
- **THEN** 子会话树与消息 SHALL 级联软删，运行中 run 与后台任务 SHALL 被取消

#### Scenario: descriptor 落库

- **WHEN** `start_task` 以类型 T 启动且 T 的生效模型为 M
- **THEN** child session `extra.subagent` SHALL 记录 `{version: 1, type: T, model: M}`

### Requirement: 后台子 Agent 执行模型

SuperAgent SHALL 通过进程内后台任务执行器 `BackgroundTaskExecutor` 执行委派子任务与后台命令任务：任务在专用守护线程的独立事件循环上运行，生命周期归属 session 而非主 run。执行器为双 kind 运行时（`subagent`：worker 编译 / child session / HITL / 追加消息；`shell`：命令直执行、有落库、无追加消息），subagent 特性经工厂与端口注入，类型维度对执行器不可见。任务状态机 SHALL 覆盖 running / awaiting_approval / completed / failed / cancelled / timed_out（停止为乐观终态：受理即落 cancelled/timed_out，无中间态；收尾异步完成）；每会话并发上限与单任务超时 SHALL 由 `subagents` 配置约束（后台命令任务超时独立约束，停止宽限期 `stop_grace_seconds` 同组配置）。

任务事实源 SHALL 在 PostgreSQL：subagent 任务为 child session 与其 run 行，shell 任务为 `t_bg_shell_job` 行。内存注册表 SHALL 定位为执行热集——仅承载活跃（queued / running）与近终态（retention 窗口内）任务条目及执行壳（future / watchdog / 追加消息队列 / 编译缓存），SHALL NOT 作为跨重启的任务事实。执行面 SHALL 保持 leader-only：任务状态的写者唯一（leader 进程），内存领先 DB 的部分仅为有界的持久化延迟。

进程重启后启动对账 SHALL 收口遗留：活跃 child run 收口为 error（`SUBAGENT_PROCESS_RESTARTED`；queued 行除外，改走排队重建）；非终态 shell 行（queued / running，不参与重建——shell 执行环境不持久化）收口为 cancelled 并注明进程重启、产出未知；pending 的追加消息行（`extra.pending_run` 标记的 user message 行）按所属任务去向分流：任务将重建排队的保留 pending 并随任务入队；任务被本次对账收口为不可续终态（error）的翻转 dropped 标记；重启前已处可续终态（completed / cancelled）的保留 pending 原状，供冷恢复重载（行保留在会话记录中）。排队任务（仅 child run 行）SHALL 按落库行（queued 状态、`created_at` 升序）重建进程内排队队列。对账 SHALL NOT 调用模型或工具。

`check_task` / `list_tasks` / `cancel_task` 对内存 miss 的任务 SHALL 回退 DB 投影回答（状态映射与内存快照复用同一函数），SHALL NOT 将可从 DB 回答的任务误报为「任务不存在」；对 DB 已终态任务的取消 SHALL 幂等返回该终态快照；对确实无任何 DB 事实的 task_id SHALL 返回可诊断提示。任务投影 SHALL 携带 `subagent_type` 字段（shell 任务为 null）。

#### Scenario: 委派后主 Agent 继续

- **WHEN** SuperAgent 在主 run 中发起 `run_in_background=true` 的委派
- **THEN** 工具 SHALL 立即返回 child session id 与「可继续其他工作」提示
- **AND** 主 Agent 本轮 SHALL 不等待子任务完成

#### Scenario: 跨轮收取结果

- **WHEN** 主 Agent 发起委派后结束当前轮次，随后新轮次中 `check_task`
- **THEN** 任务事实 SHALL 保持可查：热集内读内存快照，终态回收后读 DB 投影，任意后续轮次 `check_task` SHALL 返回终态与结果

#### Scenario: 并发上限

- **WHEN** 同一会话活跃任务数已达上限后再发起委派
- **THEN** 任务 SHALL 进入排队（queued），落库行同为 queued，不占并发槽
- **AND** 任一同会话任务落终态后 SHALL 按提交序唤醒队首任务

#### Scenario: 进程重启对账

- **WHEN** 进程重启后启动对账执行
- **THEN** 重启前活跃的 child run SHALL 被收口为 error，assistant 消息同步置 error
- **AND** 重启前 running / queued 的 shell 行 SHALL 被收口为 cancelled（错误注明进程重启、产出未知）
- **AND** 重启前 pending 的追加消息行 SHALL 按所属任务去向分流：任务重建排队的随任务保留入队；被收口为不可续终态（error）的任务，其遗留消息行翻转 dropped；重启前已处可续终态（completed / cancelled）的任务，其 pending 行保留原状（SHALL NOT 静默消失；不可续任务的遗留指示 SHALL NOT 自动执行）
- **AND** 对账 SHALL NOT 调用模型或工具

#### Scenario: 排队任务重启重建

- **WHEN** 重启前存在排队中的 child run（run 行为 queued），重启后启动对账完成
- **THEN** 系统 SHALL 按落库行 `created_at` 升序重建进程内排队队列，并发槽可用时按序唤醒
- **AND** 唤醒候选 SHALL 按落库行 `created_at` 全局升序（与所属会话无关）；会话槽满的候选留队 SHALL NOT 阻塞其他会话候选
- **AND** queued 的 shell 行 SHALL NOT 被重建（收口为 cancelled，见「进程重启对账」）

#### Scenario: 内存 miss 回退 DB 投影

- **WHEN** 主 Agent `check_task` 一个未在内存热集中的任务（终态已回收或跨进程查询）
- **THEN** 系统 SHALL 从 DB 投影返回任务状态与结果，SHALL NOT 返回「任务不存在」

#### Scenario: 任务投影携带类型

- **WHEN** 查询任意后台任务的投影
- **THEN** 投影 SHALL 包含 `subagent_type`（subagent 任务为注册类型名，shell 任务为 null）

### Requirement: 子 Agent 类型注册表与分发

系统 SHALL 提供进程内子 Agent 类型注册表：每个类型为一条不可变声明（`name` 唯一标识、`description` 供模型选择、`worker_factory` 编译配方、可选 `interrupt_on` 审批配置、可选 `model_id` 模型绑定），在 SuperAgent 装配期注册。子 Agent 的模型 SHALL 在装配/配置层按类型解析：`model_id` 绑定的类型以绑定模型编译 worker，未绑定的类型沿用父 Agent 模型；`start_task` SHALL NOT 暴露模型选择参数（运行时只选类型，不选模型）。`start_task` 的 `subagent_type` 参数 SHALL 为必填（缺失即 schema 校验拒绝），SHALL 以注册表为枚举来源：注册成功即出现在工具参数枚举与 system prompt 类型清单中；传入未注册类型时工具 SHALL 返回可诊断错误文本，且 SHALL NOT 创建 child session。装配期检测到重名注册 SHALL 立即失败。类型注册表 SHALL 在构造处集中断言所有 worker 工具集不含后台任务工具（递归委派防线前移至装配期）。

#### Scenario: 按类型委派

- **WHEN** 模型调用 `start_task(description, prompt, subagent_type=T)` 且 T 已注册
- **THEN** 任务 SHALL 以 T 的 worker 配方编译执行，child session descriptor SHALL 记录 type=T

#### Scenario: 未知类型拒绝

- **WHEN** 模型调用 `start_task(..., subagent_type=X)` 且 X 未注册
- **THEN** 工具 SHALL 返回包含 X 与可用类型清单的错误文本
- **AND** SHALL NOT 创建 child session 或 run

#### Scenario: 缺失类型参数拒绝

- **WHEN** 模型调用 `start_task` 未携带 `subagent_type`
- **THEN** 该工具调用 SHALL 因必填参数缺失被 schema 校验拒绝，SHALL NOT 创建 child session

#### Scenario: 类型绑定模型

- **WHEN** 注册类型 T 声明 `model_id=M`，模型调用 `start_task(..., subagent_type=T)`
- **THEN** worker SHALL 以模型 M 编译，child session SHALL 记录生效模型 M
- **AND** `start_task` 的参数 schema SHALL NOT 包含任何模型选择参数

#### Scenario: 未绑定模型沿用父模型

- **WHEN** 注册类型 T 未声明 `model_id`，主 Agent 当前模型为 P
- **THEN** T 的 worker SHALL 以模型 P 编译，child session SHALL 记录 P

#### Scenario: 重名注册失败

- **WHEN** 装配期两个类型声明使用相同 name
- **THEN** 装配 SHALL 抛出异常，Agent 不进入服务

### Requirement: 单工具同异步参数（run_in_background）

委派 SHALL 只有一个工具入口 `start_task`，其参数由模型按依赖关系选择：`run_in_background`（默认 true）——true 立即返回（后台）；false 为**前台等待**——执行仍走同一后台路径（隔离 loop / 注册表 / 超时），工具 await 终态并把终态文本作为工具返回值。前台等待超过 `foreground_max_wait_seconds`（默认 600s）SHALL 自动转为后台并提示稍后 `check_task` 收果（等待经 shield 实现，取消不波及底层任务）。`subagent_type`（必填）按注册表校验与分发（见「子 Agent 类型注册表与分发」）。系统 SHALL NOT 提供第二条同步委派执行路径。

#### Scenario: 前台等待返回结果

- **WHEN** 模型调用 `start_task(description, run_in_background=false)` 且子任务到达 completed
- **THEN** 工具 SHALL 返回子任务的最终小结文本（含 child session id 供前端关联）

#### Scenario: 前台等待超时转后台

- **WHEN** 前台等待超过 `foreground_max_wait_seconds`
- **THEN** 工具 SHALL 返回「已自动转为后台」提示，任务 SHALL 继续后台执行不被取消

#### Scenario: 前台等待期间审批

- **WHEN** 前台任务中途触发工具审批
- **THEN** 工具 SHALL 持续等待至审批后续跑并到达终态；审批触达走用户面板

### Requirement: 任务身份进主 Agent graph state

子 Agent 工具面 SHALL 以 middleware 形式挂载（与既有能力 middleware 栈同构），并将任务身份写入主 Agent graph state：`bg_tasks` 按任务 id 合并保存 task_id、child_session_id、subagent_type、description 与最近一次工具交互的状态快照，随主 Agent checkpoint 持久化。任务身份 SHALL 免疫上下文压缩：压缩 SHALL NOT 丢弃 `bg_tasks`。state 定位为投影——任务状态与结果的权威来源 SHALL 永远是执行器注册表（miss 时落 DB），`check_task` / `list_tasks` SHALL NOT 信任 state 快照；终态任务 SHALL 保留在 state 中不删除。

#### Scenario: 压缩后任务清单仍在

- **WHEN** 主 Agent 会话发生上下文压缩且此前启动过后台任务
- **THEN** 压缩后的 state 中 `bg_tasks` SHALL 保留全部任务身份条目

#### Scenario: state 快照过期不误导

- **WHEN** `bg_tasks` 中某任务的快照状态为 running，而执行器中该任务已终态
- **THEN** `check_task` SHALL 返回执行器的实时终态与结果

#### Scenario: 工具写入身份

- **WHEN** `start_task` 成功启动任务
- **THEN** 该工具 SHALL 通过返回 Command 将任务身份写入 `bg_tasks`，同时向模型返回与既往一致的启动提示文本

### Requirement: task-worker 编译契约

后台 task-worker SHALL 经 async 工厂在隔离事件循环内惰性编译：worker 配方 SHALL 由任务所属 `subagent_type` 对应的注册表声明提供（v1 仅 `general`，配方与既有单一 worker 一致），LLM 客户端与 checkpointer 连接池 SHALL 绑定隔离 loop（`create_isolated_checkpointer` 独立建池，共用 checkpoint 库）。task-worker 带 checkpointer 与 SUBAGENT profile 中间件栈，且 SHALL NOT 携带后台任务工具自身（禁止递归委派）。task-worker 与主 Agent SHALL 共享同一沙箱 backend。

#### Scenario: 递归委派防护

- **WHEN** task-worker 的工具集被组装
- **THEN** 其中 SHALL NOT 出现 start_task / check_task / cancel_task / list_tasks / send_message

#### Scenario: 隔离资源绑定

- **WHEN** 后台任务首次执行
- **THEN** 其 LLM 客户端与 checkpointer 连接池 SHALL 在隔离 loop 内创建，不复用主 loop 实例

#### Scenario: 按类型取配方

- **WHEN** 任务以类型 T 启动
- **THEN** worker SHALL 由 T 声明的 worker_factory 编译；执行器状态机 SHALL NOT 因类型差异产生分支

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

### Requirement: 子会话详情与事件流

子会话正文 SHALL 读取标准会话消息（`GET /sessions/{id}/messages`，与其他会话同一协议）；实时过程 SHALL 经 run 事件流订阅（`GET /runs/{run_id}/stream`），事件词汇、编码与恢复语义 SHALL 与主会话 run 完全一致（见 `agent-delivery`）：帧级事件（`text-delta` / `reasoning-delta` / `tool-input-*` / `tool-output-available` / `context-update` / `stats-update` 等）+ run 级生命周期事件（`hitl-required` / `run.finished`）。checkpointer SHALL 只用于 LangGraph 执行恢复，SHALL NOT 作为产品消息读模型。

子会话的 assistant 消息内容 SHALL 与主会话使用同一 multipart 格式，并由共享投影逻辑生成：检索类工具（`web_search` / `web_fetch` / `search_knowledge_base`）的输出 SHALL 被解析为结构化 retrieval parts 持久化（与主 run 桥接层同构），检索工具 part 的展示输出 SHALL 为「检索到 N 条来源」摘要。子会话详情视图 SHALL 展示该子会话的来源面板（基于其落库 retrieval parts，会话内按 canonical URL 去重）。

事件流契约：

- 连接 SHALL 先订阅、再取权威快照、按 sequence 连续性重放 durable 事件（transient 事件仅在线投递，不重放）；SHALL NOT 存在按事件类型的序号豁免白名单；
- 前端 SHALL 从帧级事件自组装 assistant 投影，投影函数族与主聊天为同一实现；SHALL NOT 依赖服务端 `message.updated` 全量投影事件；
- `run.finished` SHALL 终止流并发送 `[DONE]`；
- 客户端断开（关闭详情抽屉）SHALL 立即退订（generator finally），终态 run 只发快照 + `[DONE]`、不建立订阅；
- 断流自愈 SHALL 与主聊天同模式：有界重试 + 权威 run 快照收口；重试耗尽且 run 非终态时 SHALL 向用户展示可感知的失败/重连入口，SHALL NOT 静默停留在「生成中」。

前端 SHALL 复用主 Agent 的消息渲染组件（Markdown / 工具块 / 审批卡 / 输入框）；父会话只展示带 child session 引用的轻量卡片，目录与卡片打开同一详情视图。

#### Scenario: 子会话来源同构落库

- **WHEN** 子 Agent 执行 `web_search` 并获得结果
- **THEN** 子会话 assistant 消息内容 SHALL 含对应 retrieval part（query、results、truncated），与主会话同格式
- **AND** 该工具 part 的输出 SHALL 为「检索到 N 条来源」而非原始结果文本

#### Scenario: 子会话详情展示来源

- **WHEN** 用户打开子会话详情（抽屉 / 任务目录）
- **THEN** 视图 SHALL 基于子会话落库 retrieval parts 渲染来源面板，会话内按 canonical URL 去重
- **AND** 存量子会话（无 retrieval parts）SHALL 不渲染来源面板，其余展示不变

#### Scenario: 断线重连恢复

- **WHEN** 详情抽屉重开或刷新后按游标重连
- **THEN** SHALL 从权威快照 sequence 之后连续重放 durable 事件，不重复、不丢消息
- **AND** 断线期间的 transient 事件 SHALL NOT 被补发，客户端状态仍与快照一致

#### Scenario: 帧级事件自组装投影

- **WHEN** 子 Agent run 产生 text delta 与工具调用
- **THEN** 详情抽屉 SHALL 以与主聊天相同的投影函数族从帧事件组装 assistant parts
- **AND** 流式中与终态后的渲染结果 SHALL 与落库消息回放一致

#### Scenario: 重试耗尽可见失败

- **WHEN** 详情抽屉的流订阅连续重试达到上限且 run 仍非终态
- **THEN** 视图 SHALL 展示连接失败提示或重连入口
- **AND** SHALL NOT 持续显示「正在生成」或可用停止按钮

#### Scenario: 关闭详情退订

- **WHEN** 用户关闭详情抽屉
- **THEN** SSE 订阅 SHALL 立即释放，不产生泄漏

#### Scenario: 越权访问

- **WHEN** 用户 A 读取用户 B 的子会话或 run 事件流
- **THEN** SHALL 返回 404 语义

### Requirement: 完成通知注入

后台任务到达终态时 SHALL 向所属会话的待送达通知队列写入一条通知（task_id、终态、结果预览 ≤80 字），且通知负载 SHALL 附带该子会话的**去重来源清单**（canonical URL 归一化去重、有界；结构化字段，不混入预览文本）。该会话下一次 run 启动组装输入前 SHALL drain 队列并以 `[系统通知]` 前缀注入本轮上下文（注入文本以小结为主、来源清单以有界附录段携带），注入一次性且 SHALL NOT 写入消息落库内容；主 run 桥接层 SHALL 将通知携带的来源清单登记为带 origin 标记（归属该子 Agent 任务）的 retrieval parts，落在收取发生的 assistant 消息上持久化。系统 SHALL NOT 主动为通知启动 run；前端轮询为用户侧兜底触达。`awaiting_approval` SHALL NOT 注入模型通知。

#### Scenario: 通知携带来源清单并登记

- **WHEN** 子 Agent 终态且其子会话含检索来源
- **THEN** 终态通知负载 SHALL 携带去重来源清单（有界）
- **AND** 通知注入轮的 assistant 消息 SHALL 落库带 origin（该子 Agent 任务）标记的 retrieval parts
- **AND** 注入的用户消息落库内容 SHALL 与通知注入前一致（来源只进 assistant parts）

#### Scenario: 下一轮收到通知

- **WHEN** 后台任务完成，用户随后发送新消息
- **THEN** 本轮模型输入 SHALL 以 `[系统通知]` 前缀包含该任务完成提示、有界来源附录与 check_task 指引
- **AND** 再下一轮 SHALL NOT 重复出现该通知

#### Scenario: 用户消息原文不受污染

- **WHEN** 通知被注入某轮上下文
- **THEN** 该轮用户消息的数据库持久化内容 SHALL 与用户原始输入一致

#### Scenario: 续跑通知不伪装用户输入

- **WHEN** continuation run 因通知自动创建
- **THEN** 其 user 消息落库时 SHALL 携带来源标记（`extra.source_kind = bg_task_notice`）
- **AND** 前端 SHALL 将带该标记的消息渲染为系统通知条，SHALL NOT 渲染为用户消息气泡
- **AND** 续跑事件（`bg-continuation`）SHALL 携带通知全文与 child session 引用，前端据此实时插入同形态通知条

#### Scenario: 不主动唤醒

- **WHEN** 后台任务完成但用户未再发消息
- **THEN** 系统 SHALL NOT 自行启动模型调用；前端任务面板 SHALL 显示终态

### Requirement: check_task 携带来源清单

`check_task` 收取子任务结果时，返回文本 SHALL 在终态小结后附该子会话的**去重来源清单段**（有界，受工具输出预算约束）；主 run 桥接层 SHALL 将其登记为带 origin 标记（归属该子 Agent 任务）的 retrieval parts，落在收取发生的 assistant 消息上持久化。清单为模型侧纯增益：模型 SHALL NOT 被要求在正文中复述来源清单。

#### Scenario: check_task 收取登记来源

- **WHEN** 主 Agent `check_task` 收取一个含检索来源的终态子任务
- **THEN** 返回文本 SHALL 附有界来源清单段
- **AND** 收取轮的 assistant 消息 SHALL 落库带 origin（该子 Agent 任务）标记的 retrieval parts

#### Scenario: 无来源子任务

- **WHEN** 子任务无任何检索来源
- **THEN** `check_task` 返回与通知负载 SHALL NOT 携带空清单占位

### Requirement: 来源身份与跨边界去重

来源身份 SHALL 为 canonical URL（去 tracking 参数、统一协议与 host 大小写等归一化）；子会话来源清单提取与主会话研究弧聚合 SHALL 按同一归一化规则去重，前后端 SHALL 共享同一规则（含测试对齐）。同一 canonical URL 被多个贡献者检索或引用时 SHALL 合并为单一条目并携带完整 origin 列表；去重作用域为单个子会话内与研究弧内（跨弧不去重、不渗透）。

#### Scenario: 多子 Agent 同源合并

- **WHEN** 两个子 Agent 均检索并引用了同一 canonical URL
- **THEN** 主会话研究弧聚合面板中该来源 SHALL 为单一条目，origin 列表含两个子 Agent 任务
- **AND** 面板计数 SHALL 只计一次

#### Scenario: 跨弧不渗透

- **WHEN** 相邻两个研究弧（两次真实用户消息发起）均使用了同一来源
- **THEN** 该来源 SHALL 在两个弧的面板中各出现一次，SHALL NOT 相互合并或递增对方计数

### Requirement: 子 Agent run 写操作 SHALL 对齐主链路错误契约

子 Agent run 的写操作端点（stop、HITL resume、subagent-messages）SHALL 使用类型化异常映射：资源不存在 SHALL 返回 404，状态冲突（重复决策、非法状态迁移）SHALL 返回 409，SHALL NOT 以 500 或字符串嗅探表达业务冲突。`POST /api/chat/runs/{run_id}/stop` 对子 Agent run SHALL 返回 `RunSnapshot` 契约的响应体（status 覆写 interrupted——乐观终态，受理即达）。写操作族 SHALL 与 `hitl/resume` 一致实施 CSRF 校验。

#### Scenario: stop 响应为快照契约

- **WHEN** 用户对 running 子 Agent run 调用 stop
- **THEN** 响应 data SHALL 为 RunSnapshot 形状（含 id/status/sequence 等字段）
- **AND** SHALL NOT 因响应序列化失败返回 500

#### Scenario: 重复审批决策返回 409

- **WHEN** 用户对非 awaiting_approval 的子 Agent run 再次提交审批决策
- **THEN** 系统 SHALL 返回 409 与冲突语义文案
- **AND** SHALL NOT 返回 500

#### Scenario: 写操作 CSRF 一致

- **WHEN** 客户端不带 CSRF token 调用子 Agent run 的 stop / 追加消息写端点
- **THEN** 系统 SHALL 与 hitl/resume 一致拒绝请求

### Requirement: 后台命令任务（execute run_in_background）

`execute` 工具 SHALL 保留单工具形态并增加 `run_in_background` 参数（默认 false，前台执行路径与参数引入前一致）。`run_in_background=true` 时命令 SHALL 作为 shell job 进入现有注册表、状态机、完成通知与前端任务面板管线——不经 worker 编译，直接经 agent backend 执行（local_shell 宿主机 / docker 容器内）。shell job 非对话、无追加消息；任务事实（状态机、命令、结果尾部摘要、时间戳）SHALL 落库 `t_bg_shell_job`，内存 miss 的查询 SHALL 回退该表回答；进程重启时非终态 shell 行 SHALL 由启动对账收口为 cancelled 并注明进程重启、产出未知。工具替换 SHALL 保留 `execute` 工具名（`interrupt_on` 审批按名匹配，危险命令审批仍发生在启动前）。文件系统工具与 backend 接口 SHALL NOT 受影响。

#### Scenario: 长命令后台执行

- **WHEN** 模型调用 `execute(command, run_in_background=true)`
- **THEN** 工具 SHALL 立即返回 task_id 与「可继续其他工作，稍后 check_task 收果」提示
- **AND** 命令 SHALL 在原 backend 执行环境（docker 模式为会话容器内）运行，与文件系统工具共享同一文件系统

#### Scenario: 前台行为不变

- **WHEN** 模型调用 `execute(command)` 或 `execute(command, run_in_background=false)`
- **THEN** 执行路径 SHALL 与参数引入前完全一致（同步等待、timeout 参数语义、输出截断）

#### Scenario: 收果与输出

- **WHEN** shell 任务到达 completed 并被 `check_task` 收取
- **THEN** SHALL 返回 exit code 与有界的 stdout/stderr 尾部摘要
- **AND** shell 任务 SHALL 为非对话任务：可查看、可 `cancel_task`，SHALL NOT 支持 `send_message` 续话

#### Scenario: shell 任务落库与重启收口

- **WHEN** shell 任务启动、到达终态或所在进程重启
- **THEN** 状态迁移与结果尾部摘要 SHALL 落库 `t_bg_shell_job`
- **AND** 重启前 running 的 shell 行 SHALL 被启动对账收口为 cancelled（注明进程重启、产出未知），重启后查询 SHALL 返回该终态而非「任务不存在」

#### Scenario: 超时与生命周期

- **WHEN** shell 后台任务运行
- **THEN** 其 SHALL NOT 受 subagent 任务超时约束，超时由 `shell_task_timeout_seconds` 独立控制（默认不限时）
- **AND** 会话沙箱销毁时运行中 shell 任务 SHALL 转 failed（错误注明容器回收）

#### Scenario: 完成通知复用

- **WHEN** shell 任务到达终态
- **THEN** SHALL 走与 subagent 任务相同的完成通知管线（run 内注入 / 续跑通知条），前端任务面板 SHALL 显示同形态任务卡

### Requirement: 协作式停止与部分成果回收

停止一个后台子 Agent SHALL 是「乐观终态 + 协作退出 + 成果回收」（对齐主 Agent 停止语义，决策记录 `2026-09-07-子代理停止乐观终态化`）：`cancel`（用户 stop API、主 Agent `cancel_task` 工具）SHALL 同步把任务落为 `cancelled` 终态并立即返回该快照（并发槽随之释放），执行协程取消 fire-and-forget；对已终态任务再次调用 SHALL 幂等返回。执行循环经停止信号在**静止边界**（最新消息为工具结果、或无工具调用的 AI 消息）协作退出——带未应答 tool_calls 的快照点 SHALL 先让工具节点执行完毕再退出，保证线程不残留悬空工具调用。部分成果回收与终态通知 SHALL 由执行协程自身的收口路径完成（静止边界到达时携带完整产出；宽限超时走硬杀收尾，成果从落库投影回收）；终态事件与通知以归属权保证恰好一次。停止受理期间当前步骤触发 HITL interrupt 时停止 SHALL 优先：任务按取消收尾，SHALL NOT 进入 awaiting_approval。超时 watchdog 同为乐观终态（受理即 `timed_out`）；排队与 awaiting_approval 任务的停止 SHALL 即时终态（无执行面）。

停止与续聊 SHALL 执行/意图分离：`cancelled` 与 `completed` 同为可冷恢复终态——停止后 `send_message` SHALL 经冷恢复同 thread 开新 turn（排队中的追加消息意图一并保留消费），受理时 SHALL 清除停止信号并中和旧执行协程与在飞对账任务；`failed` / `timed_out` SHALL 拒绝续聊。任务终态因 cancelled / timed_out 到达时，系统 SHALL 从子会话已落库投影中提取全部文本产出作为部分成果，以「中止前部分产出」标注写入 `task.result`，并使 `check_task` 返回与父 Agent 通知注入一致携带（终态通知 preview 从提取内容开头截取，标注前缀不占预览字符预算）。

#### Scenario: 停止请求乐观终态受理

- **WHEN** 用户对 running 子任务调用 `POST /api/chat/runs/{run_id}/stop`
- **THEN** 响应 SHALL 为 DB run 快照形状且 status 覆写为 interrupted（乐观终态，不等待收尾）
- **AND** 前端任务卡 SHALL 同步显示「已取消」（部分产出稍后由后台收口写入，check_task 可查收）

#### Scenario: 重复停止幂等

- **WHEN** 对已终态（cancelled）的任务再次调用 stop 或 `cancel_task`
- **THEN** SHALL 返回同一终态快照，不产生重复状态事件或副作用

#### Scenario: 当前步骤完整结束且不残留悬空工具调用

- **WHEN** 停止受理时子 Agent 正在执行某一步骤，或刚产出一条带 tool_calls 的 AI 消息
- **THEN** 该步骤（含其工具调用与结果）SHALL 完整结束：产出进入子会话投影并落库，thread 停在无未应答 tool_calls 的快照点后协作退出
- **AND** 子会话历史保持可追加消息续跑（cancelled 可冷恢复）

#### Scenario: 停止后续聊（执行/意图分离）

- **WHEN** 任务因用户停止落为 cancelled 后，主 Agent 或用户对该任务 `send_message`
- **THEN** 系统 SHALL 同 thread 冷恢复开新 turn（任务回到 running，排队追加消息一并消费），SHALL NOT 以「已结束」拒绝

#### Scenario: 停止受理期间触发 HITL

- **WHEN** 停止受理后当前步骤触发审批中断
- **THEN** 任务 SHALL 直接按取消收尾，SHALL NOT 进入 awaiting_approval 等待审批

#### Scenario: 部分成果回收

- **WHEN** 子任务已产出若干文本后到达 cancelled / timed_out 终态
- **THEN** `task.result` SHALL 以「中止前部分产出」标注携带子会话投影中的全部文本产出（有界截断）
- **AND** `check_task` 返回与父 Agent 通知注入 SHALL 携带同一内容，通知 preview SHALL 从提取内容开头截取（标注前缀不占预览预算）
- **AND** 主 Agent 据此可将被停任务的部分成果并入交付

#### Scenario: 停止宽限兜底

- **WHEN** 停止受理后超过 `stop_grace_seconds` 执行协程仍未到静止边界（如极长的单步骤工具执行）
- **THEN** 系统 SHALL 硬杀执行协程完成收口，终态与部分成果回收语义与协作路径一致（回收终止前最后一个完整步骤的落库投影产出）
- **AND** 硬杀路径 SHALL 走完整终态收尾（事件发布、通知、落库、排队唤醒），SHALL NOT 漏发终态通知；对账 watchdog SHALL 在协程未按约收口时强制终态

#### Scenario: 无执行面的停止即时完成

- **WHEN** 对 queued 或 awaiting_approval 任务请求停止
- **THEN** 任务 SHALL 即时终态为 cancelled（与 running 停止同为乐观受理，无收尾窗口）

#### Scenario: 工具与状态查询语义

- **WHEN** 主 Agent 对 running 任务调用 `cancel_task`
- **THEN** 工具 SHALL 返回「已取消（部分产出在后台回收中，稍后可用 check_task 查收）」；对无执行面的任务（queued/awaiting_approval/shell）SHALL 只返回「已取消」
- **WHEN** 主 Agent 对 cancelled 任务的收尾窗口内调用 `check_task` 且部分成果尚未回收完成
- **THEN** SHALL 返回「已取消」形态（result 为空），稍后重查可见部分产出

### Requirement: 输出截断的一等终止语义

子 Agent run 内模型响应以 `finish_reason=length` 截断（含工具参数被拦腰截断的调用）时，系统 SHALL 把该次截断记为一等终止事实：截断标记的作用域为单 run（turn）——同轮内 sticky（后续正常完成的步骤 SHALL NOT 降级该轮的截断终态），跨轮 SHALL NOT 传染（追加消息新轮不带旧轮标记，任务终态由最后一轮决定）。带截断标记的轮次终态 SHALL 为 `partial`（finish_reason=`truncated`）而非 completed，并 SHALL 按部分成果回收语义携带「中止前部分产出」。该语义与模型边界的截断告警（可观测性）互补，SHALL NOT 影响主聊天 run 的终态判定。

#### Scenario: 截断终态

- **WHEN** 子 Agent 的模型响应以 finish_reason=length 截断且该轮未能自愈完成
- **THEN** 该轮 run 终态 SHALL 为 partial / truncated，assistant 消息与通知 SHALL 反映截断原因并携带部分产出

#### Scenario: 单轮 sticky 不降级

- **WHEN** 同一轮内早前步骤发生截断、后续步骤正常完成
- **THEN** 该轮终态 SHALL 保持 partial / truncated，SHALL NOT 因后续步骤完成而改判 completed

#### Scenario: 跨轮不传染

- **WHEN** 早前轮次发生截断，随后的追加消息轮正常完成
- **THEN** 任务终态 SHALL 由最后一轮决定为 completed，早前轮的截断事实保留在对应 child run 历史中

### Requirement: 子会话用量统计 SHALL 与主会话同口径

子会话详情 SHALL 基于子会话 assistant 消息 `extra.usage` 重建统计（turns / steps / LLM 耗时 / 输入输出 tokens / 缓存命中），渲染复用主会话统计条的组件与模板配置；统计值 SHALL 与主会话统计条采用同一计算函数。运行中的子会话 SHALL 随终态事件更新统计；历史回放 SHALL 仅凭标准消息接口重建，无需新增专用统计 API。

#### Scenario: 历史回放

- **WHEN** 打开已完成的子会话详情抽屉
- **THEN** 统计条 SHALL 从子会话消息 usage 重建并显示（如「3 轮 · 12 步 | 输入 84K · 输出 2.1K | 缓存命中 79%」）
- **AND** 无额外网络请求

#### Scenario: 流式终态对齐

- **WHEN** 子 Agent run 从 running 到达终态
- **THEN** 详情抽屉统计 SHALL 更新为与终态落库值一致的结果

### Requirement: 任务终态回收与查询 DB 兜底

终态任务条目在内存热集中保留 `terminal_retention_seconds`（默认 3600，`subagents` 配置组）后 SHALL 被回收；热集中终态条目数超过 `terminal_reclaim_max`（默认 200）时 SHALL 按终态时间从最旧起回收。回收资格 SHALL 为收口完成的条目（终态事件已发布、终态落库成功或落库重试耗尽、通知已落库），retention 自收口完成时刻起算；收尾在途的条目 SHALL 留在热集等待，SHALL NOT 进入回收候选。终态落库失败 SHALL 有界重试，耗尽后条目允许回收，DB 投影按落库事实回答并携带「终态落库失败」可诊断标注，SHALL NOT 伪造终态。pending 投递标记为瞬态（launch 采纳 / 对账翻转 dropped），消息行随会话消息 retention 管理。

回收后的任务查询（check_task / list_tasks / 任务目录 / 任务详情）与取消 SHALL 从 DB 投影回答：subagent 任务从 child session 与 run 行按状态映射派生（结果与来源清单从子会话落库内容重建），shell 任务从 `t_bg_shell_job` 行读取；DB 投影与内存快照 SHALL 复用同一状态映射函数，多 run 的 child session 取活跃 run 优先、无活跃取最新 run 行。对 DB 已终态任务的取消 SHALL 幂等返回该终态快照。DB 投影对翻转 dropped 的追加消息 SHALL 携带 `undelivered_messages` 计数（无论因重启对账还是投递失败翻转）。

对已回收且 DB 终态可续（completed / cancelled）的任务，追加消息（send_message 与用户侧同路径）SHALL 冷恢复：从 child session `extra.subagent` descriptor（version / type / model）重新解析 worker 配方、重建执行条目并**从 DB pending 行重载追加消息队列（含 turn 参数）**后同 thread 开新 turn；重建失败（类型已注销、descriptor 缺失）SHALL 返回可诊断错误且任务回退先前终态，SHALL NOT 落 failed。

#### Scenario: 回收后查询仍可答

- **WHEN** 任务终态超过 retention 被内存回收后，模型 `check_task` 该任务
- **THEN** SHALL 返回 DB 投影的终态、结果（从子会话落库内容派生）与来源清单，SHALL NOT 返回「任务不存在」

#### Scenario: 回收上限保序

- **WHEN** 内存热集中终态条目数超过上限
- **THEN** SHALL 按终态时间从最旧起回收，活跃任务条目 SHALL NOT 被回收

#### Scenario: 回收后续聊

- **WHEN** 对已回收的 completed 任务 `send_message`「继续第二步」
- **THEN** 系统 SHALL 从 child session descriptor 重建执行条目，任务回到 running 并同 thread 开新 turn
- **AND** 结束后任务结果 SHALL 更新，查询 SHALL 反映新终态

#### Scenario: 重建失败可诊断

- **WHEN** 对已回收任务的冷恢复因类型已注销或 descriptor 缺失无法重建
- **THEN** SHALL 返回包含原因的可诊断错误，SHALL NOT 创建悬空 child session 或半初始化条目

#### Scenario: 回收后重复取消幂等

- **WHEN** 对已回收（热集移除）的 cancelled 任务再次调用 `cancel_task`
- **THEN** SHALL 幂等返回该终态快照，SHALL NOT 返回「任务不存在」或产生重复终态事件

#### Scenario: 未投递指示投影可见

- **WHEN** 重启对账将某任务的 pending 追加消息翻转 dropped 后，模型 `check_task` 该任务
- **THEN** 返回 SHALL 携带 `undelivered_messages` 计数与「重启前追加指示未执行」类提示

### Requirement: 追加消息命名一致性

后台任务的追加消息机制 SHALL 统一使用「追加消息 / message」语义命名（代码符号、文件与目录名、组件名、测试名），SHALL NOT 保留 followup 历史命名、旧名兼容别名或转发层；执行内核、服务、前端与测试对同一机制的称呼 SHALL 为同一词汇。历史叙述（决策记录、已归档 openspec 变更）SHALL NOT 受本条约束。

#### Scenario: 全仓命名门槛

- **WHEN** 对 `backend/packages`、`backend/server`、`backend/tests`、`frontend/src`、`frontend/__tests__` 执行 `grep -ri followup`
- **THEN** SHALL 零命中

#### Scenario: 无兼容别名

- **WHEN** 任一调用方引用追加消息的函数、组件或模块
- **THEN** SHALL 只存在唯一命名（如 `deliver_message` / `send_message` / `MessageQueue`），SHALL NOT 出现旧名转发、弃用标记或双轨入口
