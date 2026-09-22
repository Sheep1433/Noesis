# 设计：后台任务事实源出内存落库

对照实物：`backend/packages/noesis-core/src/noesis/agents/background/jobs/`（registry/events/settle/state/loop）、`agents/background/executor.py`、`services/subagent_session_service.py`、`services/agent_catalog_service.py`、`chat/runs/bus_redis.py`、`storage/postgres/models/`。问题来源：`docs/bug/background-executor-memory-state-audit.md`。

术语：本文统一称「追加消息」——向后台子任务追加一条消息，任务 running 时排队、终态可续时冷恢复开新 turn。代码现状符号族 followup（`deliver_followup` / `create_followup_run` / `followups` deque / `MAX_FOLLOWUPS` / API `/subagent-followup`）随本变更更名（`deliver_message` / `create_turn_run` / `pending_messages` / `MAX_PENDING_MESSAGES` / `/subagent-messages`）。模型侧工具 `update_async_task` 一并更名 `send_message`——主规格「追加消息」条款本就以 `send_message` 为模型侧工具名，此次更名是代码向规格靠拢，不保留上游别名。范围声明：仅 `send_message` 一项对齐，`start_async_task` / `check_async_task` / `cancel_async_task` / `list_async_tasks` 与主规格 `*_task` 措辞的既有分歧不在本变更内（另行小改处理）。静态引用点须同步：`subagent/roles.py` 的 `BG_TASK_TOOL_NAMES`（递归委派防线名单）与工具名契约测试；部署切换的两个在途影响面——checkpoint 里旧名工具调用解绑为工具错误（模型可按新名重试）、浏览器缓存的旧前端调 `/subagent-followup` 得 404（刷新即恢复），均接受。

## 结论

三层承载：**PG 是事实源，Redis 只用已落地的 pub/sub 通道，内存只留执行热集**。与审计文档档位 1 的差别在通道层：审计建议的 Redis list 队列与 `SET NX EX` 租约不建——`enable-distributed-sse-pubsub` 落地后执行面收敛为单 leader（advisory lock + `t_runtime_leader` 任期），任务状态只存在一个写者，DB 行本身就是队列，跨进程扇出已有信令桥。被否理由见「备选方案」。

| 层 | 承载 | 现状 → 目标 |
|---|---|---|
| PG | 任务目录与状态机事实、追加消息待投递行、shell 任务记录、终态 retention | `_TASKS` 状态事实与 followups deque 迁出；shell 从无到有 |
| Redis（已有，零新增） | 跨进程事件扇出（bg-tasks 信令桥、子会话 run 事件总线）、durable stop 命令 | 保持不动 |
| 内存 | 隔离 loop、执行中 future、watchdog/宽限定时器、compiled_agent 缓存、有界投递缓冲、活跃与近终态任务条目（热集） | 语义收窄：从「唯一事实源」变为「执行壳 + 读缓存」 |

## 关键判断

### 单写者使一致性问题退化为持久化 lag

执行面 leader-only 是本设计成立的前提：start/execute 工具只在接受主 run 的 leader 进程内触发，`bg_task_stop` 命令只由 leader 的 RunCommandConsumer 消费。因此任务状态机的写者唯一，「内存与 DB 口径分叉」不再是跨写者冲突，而是同一写者内「内存已写、落库未达」的有界窗口。读取优先级随之确定：条目在热集内 → 内存快照（比 DB 新）；miss → DB 投影。窗口只在热集内可见。回收动作只发生在「终态已成功落库」的条目上，所以 DB 兜底读到的终态必然已收敛；终态落库失败（既有「落库失败不吞事件」路径，事件与通知照发、DB 仍是 running）的条目先有界重试，耗尽后允许回收、投影按落库事实回答并携带「终态落库失败」标注（不伪造终态，处置详见风险节）——既不让 DB 兜底谎报完成，也不让条目滞留热集使有界内存目标失效。

这个前提写入 spec 的边界：任务台不因本变更获得多执行实例能力（与主 run 共享单 active backend 约束）；未来把子任务执行外置到独立执行进程时，排队序与租约随外置方案重新设计（外置即审计文档远期档位：子任务执行搬出主进程，主进程只发指令、按 thread/run 语义轮询，或整体迁到分布式执行平台），本变更不为其预留结构。

### 状态映射共用一个函数

DB 投影不引入第二套状态推导：热集内条目序列化与 DB 投影复用同一个「run 行状态 + finish_reason → 任务级状态」映射，避免两处规则漂移。映射关系（可枚举部分）：

| 任务级状态 | subagent 来源（`t_agent_run`） | shell 来源（`t_bg_shell_job`） |
|---|---|---|
| queued | queued | queued |
| running | running / retrying | running |
| completed | completed；partial + finish_reason = truncated | completed |
| cancelled | partial + finish_reason = cancelled | cancelled |
| timed_out | partial + finish_reason = timeout | timed_out |
| failed | error | failed |

截断轮沿用内存现行规则收口（run 落 partial/truncated，任务级视为已完成并带截断标注，kernel 侧仅 fallback 异常才落任务 FAILED）——DB 投影照搬该规则，不制造「回收前可续聊、回收后变 failed 不可续」的资格悬崖。`stopped` 只出现在主 run 路径与 stop API 的快照覆写，不落入子 run 行，映射不收。`hitl_pending` 等非终态 run 值到任务级状态的派生沿用内存注册表现行规则（审批中间态不在 `BgTaskStatus` 值域内，由投影语义覆盖，本变更不改状态机值域）。多 run 的 child session（追加消息产生多轮）投影取行规则：活跃 run 优先，无活跃取最新 run 行——追加消息排队期间投影应反映排队中的新 run（queued），而非上一轮的终态。

### 追加消息的 write-ahead 复用 pending user message 行

待投递消息的载体**不是新表**，是 child session 的 user message 行（`t_chat_message`）。该模式已存在：HTTP 路径的 `create_pending_user_message` 先写带 `extra.pending_run: True` 标记的 user message 行再投递，launch 事务（`create_followup_run`）在同一 DB 事务里采纳这条行（清 `pending_run`、盖 `run_id`）并创建 run 行——write-ahead 与投递确认天然原子，拒绝路径已有软删实现。本变更把工具路径（`send_message`，原 `update_async_task` → `deliver_message`）并入同一模式：入口处 `user_message_id` 为空时经 SubagentSessionPort 补写 pending user message 行，再入内存队列。两条路径一个载体，不存在第二套待投递记录。turn 参数（model_id / reasoning_effort）随 `extra` 落在消息行上——顺带修掉现状「排队参数随进程重启蒸发」的隐性丢失。

**pending 行是队列事实，内存 deque 只是执行镜像**：可续终态（completed / cancelled）的未消费 pending 行保留原状——冷恢复重建条目时从 DB pending 行重载队列（含 turn 参数），「停止后排队意图保留」的语义不随条目回收而丢失；不可续终态（failed / timed_out）的收口链把未消费 pending 行翻转 dropped（它们永无消费者，滞留只占容量配额）。采纳路径现状是整覆写 extra（清 `pending_run` 的同时会丢弃行上 turn 参数），重载消费须在采纳覆写前读取参数，或采纳改为合并保留——实现取其一，规格不指定词汇。

launch 失败**不终态化任务**：任务级终态只由执行语义（完成 / 超时 / 取消 / 执行失败）决定，消息投递失败是消息级事实——冷恢复路径失败时任务回退先前终态（可续），链式路径失败时任务保持当前状态；两种路径都把消息行翻转 dropped、向调用方返回可诊断错误（模型 / 用户可重新下达）。回退须恢复完整的先前收口态（result、completed_at、终态归属权标记——冷恢复入口已把它置 False，不还原会让回收资格判定悬空）；**冷恢复窗口内受理的停止终态获胜**，此情形不回退、按停止语义收口（乐观终态契约优先于回退）。现状 `settle_followup_prelude_failure` 把任务落 FAILED 的语义随本变更改造为「回退 + 消息行 dropped + 可诊断错误」。

launch 失败分流显式分类：确定性失败（配方 / descriptor 解析不出）直接把消息行标记翻转 dropped；瞬时故障有界重试，耗尽后翻转 dropped 并计入 `undelivered_messages`——任何失败分支都不产生滞留 pending 的行（滞留行会永久占用容量配额，恰是「队列满在落库前拒绝」场景承诺不出现的形态）。重启对账同理：任务已收口终态的 pending 消息翻转 dropped 标记，**行保留在会话记录中**（用户确实发过这条消息，隐藏它反而丢失审计线索），`undelivered_messages` 计数从 dropped 标记行派生；任务将重建排队的（queued）保留 pending 原状，随重建任务一起入队消费。标记是瞬态（pending → launch 采纳 / 对账翻转 dropped），行本身随既有会话消息 retention 管理，不新增清理任务。

容量校验随载体走：上限 10 改为对该 child session 的 pending 消息行计数，超限拒绝（HTTP 路径拒绝时沿用既有软删收尾；工具路径在写行前拒绝，不产生滞留行）。count-then-insert 存在竞态：并发 K 个请求同时读到临界计数时可超限至多 K-1 条，后果是队列比上限多几条显式下达的指示、功能无损，不为它加任务级互斥。

行为后果：工具路径追加的指示消息会在写入后即时出现在子会话记录中（此前要等链式开 turn 才落消息行）——与 HTTP 路径行为一致，属统一而非偏离。

### 冷恢复复用既有 descriptor

任务从热集回收后仍可续聊：`deliver_message` 对内存 miss 但 DB 终态可续（completed / cancelled）的任务，读 child session `extra.subagent` descriptor（version/type/model）重新解析 worker 配方，重建执行条目后走既有冷恢复链。descriptor 的这一消费者在主规格「子 Agent 会话身份」中已声明（「供进程重启后重建 worker 的路径按类型与模型取配方」），本变更是把它从「重启后」扩展到「回收后」。

### 重启对账补齐两类缺口

现有对账只覆盖有 run 行的 subagent（`reconcile_orphaned_runs` → error/SUBAGENT_PROCESS_RESTARTED，且把 queued 也算活跃收口）。本变更扩展并修正：

- **queued 行改走排队重建（仅 child run 行）**：既有对账把 queued 的 child run 收口为 error，与「排队任务重启后应继续执行」矛盾——收口集合 SHALL 排除 queued 行，queued 的 child run 行按 `created_at` 升序重建进程内排队队列（并发槽判定复用既有 drain 逻辑；重建时 running 任务已全部收口为终态，槽位从零起算，唤醒至槽满即止）。shell 行**不参与重建**：shell 的执行 backend（local_shell 宿主机 / docker 会话容器）不持久化，重启后无从重建执行环境，queued 与 running 一并收口 cancelled。排队序不再持久化专列（被否，见备选方案），重启后顺序以落库时间为准，与原 `submit_seq` 序的偏差限于同时刻提交的并列任务。
- 非终态 `t_bg_shell_job` 行（queued / running）→ cancelled，error 注明进程重启、产出未知（命令本体随宿主进程生死不可考，不猜测结果；queued 的亦不重建，理由见上条）。
- pending 的追加消息行（`extra.pending_run` 标记）**按所属任务去向分流**：任务将被重建排队的（queued），行保留 pending 原状并随任务一起入队消费——「任务继续执行、指示却被丢弃」在语义上自相矛盾，且恰是本变更要消除的蒸发变体；任务被本次对账收口为不可续终态（error）的，标记翻转 dropped；重启前已处可续终态（completed / cancelled）的，行保留 pending 原状供冷恢复重载——可续任务的排队意图跨重启存活，与「停止后排队意图保留」同语义。不自动重投不可续任务的遗留指示：重启后父会话上下文与任务时效已变，静默代用户执行指令有越权风险；取而代之，check/list 的 DB 投影携带 `undelivered_messages` 计数，模型与用户可据此决定是否重新下达。

## 备选方案

**排队与租约：Redis list + `SET NX EX`（审计文档档位 1 建议）——否。** 执行面单 leader 下不存在跨 worker 出队竞争，Redis list 只会制造第二份需要重建的队列事实；执行所有权已由 leader 任期（term fencing）表达，任务级租约是同一互斥的第二次实现。代价：接受单 leader 的后台任务吞吐上限（与主 run 同一约束）。若未来子任务执行外置到独立执行进程，队列与租约随外置方案重设计，本变更不预留。

**追加消息载体：独立 write-ahead 表 `t_bg_task_followup`——否。** HTTP 路径已用 pending user message 行承载待投递意图、launch 事务采纳行并确认投递——独立表会给同一意图造第二条并行载体：内容双存、两套投递确认机制、重启对账要同时扫两张表。复用消息行的代价是投递状态（`pending_run` / dropped 标记）出现在消息 `extra` 里——但该标记本就随 HTTP 路径存在于消息行，属既有事实域的补全而非新耦合。

**终态回收范围：只清 failed/timed_out——否。** 可续终态（completed/cancelled）是存量主体，豁免它们等于不解决内存增长；冷恢复重建的配方来源（descriptor）现成，回收可续任务的代价是一次 DB 读 + 一次配方解析。

**重启后未投递追加消息自动重投——否。** 意图保留与越权执行的边界：write-ahead 已保证指令内容不蒸发（行在库、可审计），对账标记 dropped 加投影可见让「是否继续执行」回到人与模型的下一次判断。自动重投把重启前的不完全上下文变成静默执行的授权，风险不对称。

**排队序持久化（新增 sequence 列）——否。** 重启重建的排序键用行 `created_at` 已足够：全局公平性要求「单会话不饿死其他会话」，不要求毫秒级提交序复刻；为并列任务保序加一列序列号，是维护成本大于收益的投机精度。

## 语义消费者说明

| 新增产物 | 本变更内的真实消费者 |
|---|---|
| pending user message 行的标记扩展（`extra.pending_run` / dropped 翻转 / turn 参数随行） | deliver_message 全路径 write-ahead（工具路径经 SubagentSessionPort 补写 pending 行）；launch 事务采纳行即投递确认（既有机制）；重启对账按所属任务去向分流（重建排队的保留、终态的翻转 dropped）；队列容量以 pending 计数判定 |
| `t_bg_shell_job` | shell 任务的 check/list/目录 DB 兜底；终态回收后查询兜底；重启对账收口非终态行 |
| `undelivered_messages` 投影字段 | 重启后 check_task 对含未投递指示的任务返回计数提示，模型可转告用户并决定是否重新下达 |
| `terminal_retention_seconds` / `terminal_reclaim_max` 配置 | 长跑进程 `_TASKS` 有界：运营场景（长时间运行的单 backend 部署）内存不随历史任务数增长 |
| 冷恢复重建路径（descriptor → worker 配方） | 用户对 1h 前完成/取消的任务继续追问（UI 追加消息与模型 send_message 同路径） |

## 风险与边界

- **不解决全应用多 worker**：主 run 的 RunManager 与 advisory lock 仍在进程内（审计文档已划界）。任务台的多实例诉求在 leader-only 前提下不成立，无需本变更背书。
- **投影派生的成本**：DB 兜底查询发生在内存 miss 后（终态任务、跨进程查询），频率低；活跃任务的查询全走热集，不新增热路径开销。
- **回收资格与收尾窗口**：回收候选 SHALL 满足「终态事件已发布、终态落库成功或落库重试耗尽、通知已落库」——乐观终态使条目在受理时刻即呈终态，而部分成果回收、通知落库由协程异步收口（跨 loop DB 往返，可达秒级）；批量终态（如父会话软删级联取消）触发上限回收时，收尾未完成的条目不得被移除。retention 自收口完成时刻起算，收尾未完成的条目留热集等待，不进回收候选（落库重试耗尽后的处置见下条）。drain 只统计占槽状态（终态不占槽），与回收无竞争。
- **终态落库失败的处置**：现状落库失败只记日志（run 行永远 running）。本变更改为有界重试（沿用主链路 persistence 超时重试模式）；耗尽后条目允许回收（否则热集无界），DB 投影按落库事实回答并携带「终态落库失败」可诊断标注——不伪造终态，对齐主 run「持续持久化失败不伪造 completed/error」的原则。该窗口内若进程重启，启动对账把遗留 run 收口 error，自然收敛。
