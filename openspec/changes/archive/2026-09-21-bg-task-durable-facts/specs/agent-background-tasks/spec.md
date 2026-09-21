# agent-background-tasks Delta

## MODIFIED Requirements

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

### Requirement: 追加消息（子会话追加 turn）

`send_message(task_id, message)`（模型侧）与 `POST /api/chat/sessions/{id}/subagent-messages`（用户侧，人 / 模型同路径）SHALL 为同一 child session 追加一条 user message 与一个新的标准 `TAgentRun`（下文统称追加消息），SHALL NOT 以中途注入方式改写当前 turn 的模型输入：

- **write-ahead 持久化**：任何路径（模型工具 / 用户 API）追加的消息 SHALL 在进入内存队列前先落库为 child session 的 pending user message 行（`extra.pending_run` 标记，携带 turn 参数）；工具路径由 `deliver_message` 入口经 SubagentSessionPort 补写该行，与 HTTP 路径共用同一载体，SHALL NOT 引入第二套待投递记录。投递确认 SHALL 复用 launch 事务对 pending 行的采纳（清 `pending_run`、盖 `run_id`），SHALL NOT 以独立更新留出「已执行却停在 pending」的崩溃窗口。**pending 行是队列事实，内存队列只是执行镜像**：可续终态（completed / cancelled）的未消费 pending 行保留原状，冷恢复重建条目时 SHALL 从 DB pending 行重载队列（含 turn 参数）；不可续终态（failed / timed_out）的收口 SHALL 把未消费 pending 行翻转 dropped。投递失败 SHALL NOT 终态化任务：任务级终态只由执行语义决定，冷恢复路径失败时任务 SHALL 回退先前终态（恢复完整的先前收口态，SHALL NOT 留下悬空的回收资格判定），链式路径失败时任务 SHALL 保持当前状态，消息行翻转 dropped、调用方收到可诊断错误；冷恢复窗口内受理的停止终态 SHALL 获胜（此情形不回退，按停止语义收口）。launch 失败分流：确定性失败（配方 / descriptor 解析不出）SHALL 直接翻转 dropped 标记，瞬时故障 SHALL 有界重试、耗尽后翻转 dropped——任何失败分支 SHALL NOT 留下滞留 pending 的行。重启对账对 pending 消息行按所属任务去向分流：任务重建排队的随任务保留入队；任务被收口为不可续终态（error）的翻转 dropped；重启前已处可续终态（completed / cancelled）的保留 pending 原状，SHALL NOT 静默消失（行保留在会话记录中）。队列上限（10）SHALL 以该任务的 pending 消息行计数判定，超限 SHALL 在落库前拒绝（HTTP 路径拒绝时软删该消息行）。前端 SHALL 对 pending 标记消息呈现待执行状态、对 dropped 标记消息呈现未执行标注，SHALL NOT 将 dropped 消息呈现为已执行。
- 任务 running：消息排队（FIFO，上限 10）；当前 turn 结束后 executor SHALL 同 thread 链式开新 turn，队列清空前任务保持 running。
- 任务 awaiting_approval：消息入队，审批 resume 完成本 turn 后由同一条链消费。
- 任务 completed / cancelled：SHALL 冷恢复——同 thread 追加消息开新 turn，任务回到 running，结束后更新结果（cancelled 可续为执行/意图分离语义：停止只终止执行，续聊意图保留）。对已从内存热集回收的任务，冷恢复 SHALL 先从 child session descriptor 重建执行条目再开新 turn。
- 任务 failed / timed_out：SHALL 返回错误说明，不可续。
- 每条追加消息的 turn SHALL 支持逐 turn 覆盖执行参数：`model_id`（现有）与 `reasoning_effort`（新增，可选）；用户侧 API 请求体新增可选 `reasoning_effort` 字段，缺省 SHALL 继承任务创建时的档位，旧客户端不传字段时行为不变。turn 参数在排队期间 SHALL 与消息绑定，链式开新 turn 时逐条生效。

#### Scenario: 运行中追加指示

- **WHEN** 任务 running 时投递「聚焦中文源」
- **THEN** 当前 turn 结束后子 Agent SHALL 以该消息为新 turn 接续推理（可多轮工具调用）
- **AND** 新 turn 结束前消息 SHALL NOT 消失或重复

#### Scenario: 工具路径追加不蒸发

- **WHEN** 模型经 `send_message` 向 running 任务追加指示，进程在该指示被执行前重启
- **THEN** 该消息行 SHALL 保留在子会话记录中并翻转 dropped 标记（内容与下达时间可审计）
- **AND** 该任务的查询投影 SHALL 携带未投递计数提示，SHALL NOT 静默消失或被自动重新执行

#### Scenario: 排队任务的指示随任务保留

- **WHEN** 排队中（queued）的任务存在 pending 追加消息，进程重启
- **THEN** 对账 SHALL 保留该 pending 消息行并随任务重建入队，任务被唤醒后按序消费
- **AND** 该行 SHALL NOT 被翻转 dropped 标记（任务未终态）

#### Scenario: 队列满在落库前拒绝

- **WHEN** 某任务 pending 的追加消息已达上限（10）后再追加
- **THEN** SHALL 返回「补话队列已满」类错误，提示等待当前轮完成或合并指示
- **AND** SHALL NOT 产生滞留的 pending 消息行

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

#### Scenario: 投递失败不终态化任务

- **WHEN** 向 cancelled 任务追加消息触发冷恢复，但 descriptor 解析失败（类型已注销）
- **THEN** 任务 SHALL 回退先前终态（cancelled，保持可续），消息行 SHALL 翻转 dropped
- **AND** 调用方 SHALL 收到含原因的可诊断错误，SHALL NOT 收到任务 failed 终态

#### Scenario: dropped 消息前端呈现

- **WHEN** 子会话抽屉渲染一条被对账翻转 dropped 的追加消息
- **THEN** 该消息 SHALL 呈现「未执行」标注，SHALL NOT 呈现为已执行的正常用户消息
- **AND** pending 标记的追加消息 SHALL 呈现待执行状态（区别于已被采纳执行的消息）

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

## ADDED Requirements

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

## RENAMED Requirements

- FROM: `### Requirement: Followup 续话（子会话追加 turn）`
- TO: `### Requirement: 追加消息（子会话追加 turn）`
