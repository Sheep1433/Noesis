## Context

Noesis 当前由 `RunService` 创建数据库 Run，进程内 `RunManager` 持有 producer、projection、subscriber 和短期 buffer，PostgreSQL 保存 assistant checkpoint 与终态。`backend/server/main.py` 在 lifespan 获取一个全局 PostgreSQL advisory lock；第二个 worker 获取失败即退出，因此多个用户和多个 Tab 共享同一进程。

现有容量脚本已覆盖 100 active Run 和 250 subscriptions，尚无证据要求并行扩展 Agent producer。真正缺口是 Web/SSE 进程不能扩展：FastAPI worker 不共享内存，非 owner 无法访问 `RunManager`。Redis 官方文档明确 Pub/Sub 是 at-most-once，离线或断连期间的消息永久丢失，因此它只能作为实时通知层，不能替代 PostgreSQL snapshot 与终态。

本设计提供两种显式运行模式，但只保留一套Run状态机。`memory`模式用于无需Redis的单backend部署；`redis`模式采用一个 execution leader + 多个 Web worker。两种模式都经过 queued Run、leader dispatcher、durable command、checkpoint、sequence 与统一subscription handle，不恢复请求线程直接启动producer的旧路径；差异只在实时通知bus及是否允许follower进程ready。

## Goals / Non-Goals

**Goals:**

- 多个 backend 进程可以同时 ready并处理 Web/API/SSE 请求。
- 同一时间只有一个 execution leader创建和持有 Agent producer。
- 任意 worker 上的多个 Tab均可实时观察同一 Run。
- Pub/Sub 消息或 wake-up 丢失后，系统可从 PostgreSQL snapshot、queued Run 和 durable command恢复。
- stop、HITL resume保持鉴权、幂等和现有终态边界。
- leader、Redis 或 worker故障不会产生第二 producer或伪造成功。
- 开发者可以通过一个显式环境变量切换memory/redis adapter，且错误配置不会静默降级。

**Non-Goals:**

- 不扩展为多个 execution leader，不按 Run 分片或迁移运行中 producer。
- 不增加按Run lease、运行中owner takeover或多个execution分片；只保留一个全局leadership term防止失锁旧leader继续claim或落库。
- 不使用 Redis Streams、Redis lock、Celery或Temporal。
- 不自动重放模型、工具或无法确认的外部副作用。
- 不迁移 `TEST_CASE_QA` 独立旧 SSE。

## Decisions

### 1. Advisory lock统一承担execution ownership，Redis模式才允许Web follower

每个进程生成唯一且不可配置复用的 `instance_id`，通过专用 PostgreSQL连接非阻塞竞争现有 application advisory lock。每次成功获得lock后，在单行 `t_runtime_leader` 中原子递增 `leader_term` 并记录 `instance_id`。该行同时固定 `cluster_id`；首个实例初始化，后续实例配置不同值时启动失败，避免共享同一PostgreSQL却发布到不同Redis namespace：

- 获锁者进入 execution leader角色，启动 Run dispatcher/recovery、scheduler、memory dream、Telegram和Feishu runtime。
- `redis`模式下，未获锁者继续启动为 Web worker，不运行上述 singleton runtime。
- `memory`模式下，未获锁者启动失败；进程内bus无法跨进程传播事件或命令，不能伪装成可横向扩展。
- follower持续等待 leader变更；leader使用专用连接的短周期heartbeat检测失锁，并为每次任期创建不可复用的本地leadership token。token失效后，dispatcher、publisher、command consumer和persistence callback都必须停止接受新工作。

只有execution leader可以把queued Run claim为running并调用 `RunManager.start()`。claim在同一SQL条件中验证 `t_runtime_leader.leader_term`仍等于当前term，并将Run的 `owner_instance_id` 与新增 `owner_term` 写为当前任期。所有checkpoint、状态推进和terminal CAS均验证active状态与 `(owner_instance_id, owner_term)`；新term建立后旧leader不能claim新Run。新leader获得lock并提交新term后先执行recovery：以新term作为recovery writer，将旧term已进入running/retrying/hitl_pending的Run统一收口为 `interrupted/server_restart`，并更新权威snapshot的owner term；未被claim且 `owner_instance_id IS NULL` 的queued Run保留并正常dispatch。

这里的全局term不是按Run lease：它不允许运行中迁移，也不需要定时更新每个Run，只负责在advisory lock连接丢失而旧进程尚未察觉的窗口中拒绝旧任期的新claim和迟到数据库写入。

该选择复用已经验证过的唯一 owner边界。未来只有当 execution leader的Agent容量成为实测瓶颈时，才另开变更设计按Run分片与fencing。

### 2. Run 创建与 producer 启动解耦

`POST /api/chat/runs` 在任意worker执行同一数据库事务：校验用户/session和`client_request_id`，完成slash command改写与本轮模型/运行参数解析，写入user message、assistant骨架及 `queued + owner_instance_id=NULL` 的Run，然后返回Run身份。Run新增受schema约束的 `launch_payload`，保存启动producer所需的不可变、可序列化输入；不得依赖请求进程内的 `CreateRunRequest`、session状态快照或 `CurrentUser` 对象。

dispatcher只根据Run、`launch_payload`和数据库中的用户记录重建执行上下文。launch payload不得保存密码、Cookie、CSRF、API key等认证秘密；用户在dispatch前被删除或禁用时，Run按明确start error收口。解析后的model identity必须写入launch payload，用户随后修改默认模型不得影响queued Run。

事务提交后通过所选Run bus发布轻量 `run-created(run_id)` wake-up。execution leader收到后先检查本地容量，再从PostgreSQL读取并以 `queued + owner IS NULL + 不存在pending stop` 条件claim为running，然后创建本地RunHandle。为防通知丢失或进程在提交后崩溃，leader在两种模式下都按有界周期扫描queued Run；数据库唯一约束继续保证同session只有一个active Run。wake-up失败不回滚已提交的创建事务。

claim成功后若 `RunManager.start()`、Agent装配或producer注册在同步启动阶段失败，leader必须将该Run和assistant骨架收口为明确error，不能保留没有内存owner的running行。进程在claim后崩溃则由下一任leader recovery收口为interrupted。

### 3. Run bus只广播实时通知，并提供memory/redis adapter

新增最小 `RunBus` port与 `InMemoryRunBus`、`RedisRunBus` 两个adapter。port只表达RunEvent publish/subscribe与dispatch/command wake-up，不暴露Redis client、channel或连接状态给Service。leader本地RunManager继续负责projection、sequence和持久化；每个已分配sequence的事件通过所选bus发布，SSE订阅层统一消费该bus：

```text
noesis:{environment}:run:{run_id}:events
```

两种adapter使用同一版本化envelope，包含固定 `schema_version`、`run_id`、`owner_instance_id`、`owner_term`、`sequence`、`attempt_id`、`event_type`和受大小限制的payload。subscriber只接受与权威Run owner term一致的event；新leader提交term并完成recovery后，旧term的迟到event必须忽略。终态事件必须先完成PostgreSQL terminal transaction再publish；CAS loser不得发布自己的候选终态，只能发布数据库权威snapshot replacement。

bus publish不得从多个event协程直接并发执行。每个本地RunHandle使用一个有界outbound queue和单consumer publisher，严格按已分配sequence发布；terminal也进入同一queue，避免越过尚未发布的普通event。queue溢出或publish失败时记录gap并丢弃实时通知，不能阻塞producer或改变Run状态，subscriber通过snapshot恢复。

每个worker只在本进程首个SSE subscriber加入某Run时创建一个Run hub。该hub共享一份bus subscription、一份握手buffer和一个snapshot reconciliation任务，再向本进程多个Tab fan-out；最后一个Tab离开后整体释放。`memory`模式下producer与hub位于同一进程，`redis`模式下可能跨进程，但使用相同hub状态机。API在订阅前必须按 `(run_id,current_user_id)` 鉴权，Redis channel名称不得包含用户输入。

选择Pub/Sub而不是Streams，是因为PostgreSQL已经承担恢复和终态权威；当前需求是在线广播，不是建立第二套durable event log。

### 4. 所有订阅使用同一subscribe-first握手

“先读snapshot再订阅bus”存在窗口，会永久漏掉两步之间的事件。所有订阅必须按以下顺序：

1. 完成Run鉴权和本进程subscription配额检查。
2. 建立本地有界queue，调用Run bus subscribe并等待adapter acknowledgment；到达的event先放入握手buffer。Redis adapter必须等待Redis server subscription acknowledgment，memory adapter同步确认。
3. 读取PostgreSQL权威Run snapshot。
4. 丢弃buffer中 `sequence <= snapshot.sequence` 的重复事件；从 `snapshot.sequence + 1` 开始连续apply。
5. 若首条或中间event出现gap，停止apply后续delta，重新读取snapshot；恢复连续后再继续。

active Run增加独立于新event到达的有界周期checkpoint flush，但仅在 `last_sequence > last_persisted_sequence` 时提交。这样即使某个非语义event恰好在subscriber离线期间发生、之后长时间没有新event，数据库snapshot也会在明确时间上限内追上，又不会重复写入未变化snapshot。

bus subscription重建、worker重连或周期性reconciliation也执行同一握手。握手buffer和恢复重试均受事件数、字节数、时间与并发上限控制；超限时关闭该SSE，让浏览器按现有重连协议重新建立，不能无界积压。

`RunService.subscribe()`返回统一的subscription handle，包含snapshot、queue和幂等 `close()`；本地与远端实现均遵守同一接口。`chat_api.py`只消费并关闭handle，不得直接调用进程全局 `run_manager.unsubscribe()`，否则follower没有本地RunHandle时会泄漏或误报。

### 5. stop 与 HITL 使用 PostgreSQL durable command + bus wake-up

新增 `t_agent_run_command`，字段包括 `command_id`、`run_id`、`user_id`、`type`、幂等键、payload、状态、创建/claim/完成时间和结果摘要。stop与HITL Service在同一事务中完成权限/状态校验和command写入，然后向：

```text
noesis:{environment}:execution:commands
```

通过所选Run bus发布只含command identity的wake-up。leader从数据库claim后再次校验Run和interrupt状态，再调用现有本地stop或resume入口。leader在两种模式下都低频扫描pending command，因此通知丢失不会丢操作。

stop使用 `(run_id, stop)` 作为稳定dedupe identity；HITL使用 `(run_id, interrupt_id)`，并保存decision payload digest。相同identity与相同digest返回同一command；相同interrupt但不同digest返回409，避免用户修改决策后覆盖已受理命令。stop最多触发一次cancel/terminal transaction；重复或过期HITL command标记为no-op/rejected，不启动第二segment。

stop/HITL API保持项目统一HTTP 200响应，但数据明确返回 `command_id`、`command_status=accepted|completed|rejected` 与最新Run snapshot。`accepted`只表示durable command已提交，不表示producer已经停止或恢复；前端立即订阅/继续订阅同一Run并显示“正在停止”或“正在继续”，直到SSE/snapshot出现权威状态。API可短暂等待快速ack，但超时仍返回accepted，不能仅凭Redis publish成功返回completed，也不新增仅用于轮询command的公开API。

queued Run收到stop时，command consumer直接以CAS将其收口为partial/stopped而不创建producer；dispatcher claim条件必须排除pending stop。若dispatcher先成功claim为running，stop则按正常active Run取消路径执行。两种事务顺序都只能得到“未启动即停止”或“启动后取消”之一，不能出现stop已确认但producer随后启动。

### 6. bus与leader故障采用明确且不自动切换的降级

`redis`模式下Redis是新Run实时调度依赖，但不能让整个Web入口因其故障而被负载均衡摘除：

- Redis不可用而PostgreSQL和Web依赖正常时，liveness/readiness端点仍保持可路由并明确返回 `degraded` 依赖状态；`POST /api/chat/runs`单独返回503并拒绝新Run。
- 已有leader producer继续写PostgreSQL checkpoint和终态；本地subscriber仍可实时接收，远端subscriber通过有界snapshot轮询恢复。
- durable command仍可落库并由leader补扫；API在ack前不宣称成功。
- Redis恢复后，remote subscription执行subscribe-first握手，不直接续接旧sequence。

`memory`模式不探测Redis，也不会在运行中切换到Redis；进程内bus异常按进程故障处理。若第二个backend连接同一业务库并选择memory模式，必须在接收流量前因拿不到exclusive execution lock而失败。运行模式只能在所有active Run完成或被明确收口、进程重启后切换，禁止热切换造成两种bus同时活跃。

降级只阻止新Run进入系统；已有Run的查询、snapshot stream、stop和HITL command仍须可用，避免基础设施降级反而使用户无法停止高风险操作。只有PostgreSQL或HTTP核心依赖不可用时，Web readiness才返回503。

leader故障时，新leader取得advisory lock后先执行recovery并发布权威snapshot通知，再dispatch未claim的queued Run。旧leader的checkpoint和终态会被数据库active-status CAS阻止覆盖recovery终态；已运行工具的外部结果继续按unknown outcome处理，不自动重放。

优雅关闭时，leader先停止接收和dispatch新Run，按既有drain策略完成或取消本地producer并停止singleton runtime，最后才释放advisory lock。不得先释放lock再drain，否则新leader会把仍在运行的任务误判为orphan。

### 7. 多进程lifespan职责必须拆清

数据库migration不能由多个FastAPI worker无保护地并发执行。`init_database()`使用独立migration advisory lock，等待schema就绪后各worker再继续启动。该lock与execution leader lock使用不同key和连接。

checkpointer、知识库读服务等每个HTTP进程需要的依赖可以各自初始化；scheduled task、memory dream、Telegram与Feishu只跟随execution leader生命周期。推荐生产部署采用“一容器一Uvicorn进程 + 多容器副本”，避免每个worker复制大量内存；同容器 `--workers >1` 仍须通过相同测试，但不是默认部署形态。FastAPI官方也明确多个worker不共享进程内内存。

### 8. 配置、指标与验收

使用必填 `NOESIS_RUN_BUS_BACKEND=memory|redis` 选择adapter。它选择的是backend内部Run实时通知与唤醒bus，不改变浏览器SSE协议，因此不命名为 `SSE_MODE`。未知值、配置缺失、`redis`模式连接失败均fail-fast；绝不根据是否存在 `REDIS_URL` 自动猜测模式，也不在运行中fallback。两种模式都经过同一dispatcher、command和subscription Service，禁止为memory模式恢复请求内直接start producer或直接操作RunManager的捷径。

启动配置明确分为：

- `.env` / EnvSecrets：必填 `NOESIS_RUN_BUS_BACKEND`。选择`redis`时额外必填 `REDIS_URL` 与 `NOESIS_CLUSTER_ID`；`REDIS_URL` 使用 `redis://` 或 `rediss://` 表达认证/TLS，`NOESIS_CLUSTER_ID`只允许稳定的字母、数字、`-`、`_`，用于Redis channel namespace并与PostgreSQL runtime cluster identity交叉校验。选择`memory`时不连接Redis；即使环境中残留Redis配置也只记录一次明确warning，运行状态必须显示实际adapter，避免操作者误判。
- `config.yaml` 的 `distributed_runs`：连接/命令超时、连接池、publisher/handshake buffer、leader heartbeat、queued/command scan、checkpoint/reconciliation间隔等非敏感运行参数。
- `instance_id`、leader/follower角色与 `leader_term`均由运行时生成或选举，禁止通过环境变量强制指定。

模式或条件配置缺失、cluster id与同一数据库已有值不一致或格式错误时进程启动fail-fast；Redis模式在启动探测的有界重试内不可达时也启动失败。运行期间Redis断开才进入前述degraded状态。`scripts/run.sh dev`可显式注入memory，`prod`与Docker Compose模板显式注入redis并启动/检查Redis；命令行参数可作为设置同一环境变量的便利入口，但不得形成第二套配置来源。

指标至少包括：leader状态/切换、queued dispatch lag、owned Run、local/remote subscription、Redis publish/reconnect/latency/payload、handshake buffer、sequence gap、snapshot reconciliation、periodic checkpoint、command pending/ack/rejected/timeout，以及本地/跨进程event-to-client latency。

现有RunManager subscription quota是进程内计数，多worker后不得继续命名为部署级global limit。首版对每worker、每用户和每Run设置本地硬上限，并由网关设置部署级连接上限；容量与安全评估按 `worker_count × local_limit` 计算最坏上界。暂不为精确全局计数引入易泄漏的Redis连接租约。

容量验收使用至少两个backend进程，仍保持一个execution leader，覆盖100 active Run、每Run 2–3 Tab，并让部分SSE固定连接follower。若结果显示execution leader达到CPU、event-loop、内存或Run上限，再以数据决定是否设计多execution owner。

## Risks / Trade-offs

- [Pub/Sub at-most-once会丢消息] → subscribe-first握手、sequence gap检测、周期checkpoint和PostgreSQL snapshot恢复。
- [创建或command wake-up丢失] → queued Run与command均先落库，leader周期补扫。
- [leader失锁检测存在时间窗] → 全局leader term约束claim/checkpoint/terminal，Redis envelope携带term；不启动第二producer，recovery标记unknown outcome并拒绝旧term迟到写入。
- [leader仍是Agent执行容量上限] → 当前容量已满足目标；先观测，达到明确阈值后再设计分片，避免本次引入lease/fencing。
- [Redis故障降低远端实时性] → 暂停新Run，已有Run继续权威持久化，远端使用有界snapshot恢复。
- [多worker重复后台任务] → singleton runtime严格绑定execution leader生命周期，并做双实例集成测试。
- [握手或gap恢复造成内存增长] → buffer、订阅、重试和超时全部有界，超限只断开单个subscriber。
- [多worker放大subscription总量] → worker本地硬上限 + 网关部署级上限，并按副本数计算容量；不伪称进程内计数是全局值。
- [Redis payload包含聊天增量] → 私网、认证/TLS、payload限制和日志脱敏，不持久化Pub/Sub内容。
- [memory/redis分支演变成两套业务实现] → adapter只实现最小Run bus port；共享契约测试覆盖相同envelope、顺序、关闭和错误语义，dispatcher、durable command、checkpoint、reconciliation及SSE协议不分叉。
- [memory模式被误用于多实例] → exclusive advisory lock获取失败即启动失败，健康信息明确报告`run_bus_backend=memory`与`multi_worker_supported=false`。

## Migration Plan

1. 增加Run bus port及memory/Redis adapter、显式模式配置、runtime leader term、Run launch payload、durable command migration和独立migration lock；先以memory单进程验证共享状态机。
2. 将Run创建改为只写queued Run，由当前leader dispatcher启动；完成创建幂等和wake-up丢失测试。
3. 将全局lock改为leader角色，followers保持Web ready；singleton runtime绑定leader生命周期。
4. 接入Redis RunEvent、subscribe-first握手、周期checkpoint和gap恢复。
5. 接入stop/HITL durable command与补扫。
6. 更新health/readiness、Compose和多进程E2E；以一个backend切换Redis，再扩为两个，并执行memory/redis共享契约测试。
7. 验收后删除请求进程直接start producer的旧分支；仅保留memory模式必要的单实例启动门禁，不保留两套业务实现。

回滚前停止创建新Run并drain/收口active Run，将backend缩为一个进程后回滚代码。新增command表先保留，确认旧版本不读取后再单独清理。

## Open Questions

- 生产Redis使用单节点、Sentinel还是托管高可用；不影响应用层可靠性边界。
- 周期checkpoint和command scan默认间隔通过故障测试决定，Spec只要求有界恢复。
- execution leader容量达到什么指标时才启动按Run分片设计，需要在本次多worker容量报告中记录阈值。
