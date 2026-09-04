## ADDED Requirements

### Requirement: 多 Web worker SHALL 共享唯一 execution leader

系统 SHALL 允许多个backend进程同时ready并处理HTTP/SSE请求，但同一部署环境同一时刻 SHALL 只有一个持有PostgreSQL advisory lock的execution leader。每次成功选举 SHALL 在持久化runtime leader记录中原子产生单调递增的全局 `leader_term`；Run claim、checkpoint、状态推进、terminal和Redis event SHALL 携带并校验该term。runtime leader记录 SHALL 固定同一PostgreSQL数据库对应的cluster id，配置不一致的实例 SHALL 启动失败。只有leader可以claim queued Run、创建producer及启动scheduler、memory dream和messaging runtime；follower SHALL NOT 因未获leader lock而退出。

#### Scenario: 第二个 backend 启动

- **WHEN** 已有进程持有execution leader lock，第二个进程连接同一PostgreSQL和Redis
- **THEN** 第二个进程 SHALL 以Web worker角色进入ready并接受API/SSE请求
- **AND** SHALL NOT 启动Agent producer或singleton runtime

#### Scenario: leader lock丢失

- **WHEN** execution leader的专用lock连接失效
- **THEN** 该进程 SHALL 立即停止dispatch新Run、取消本地producer并停止singleton runtime
- **AND** 其它进程 SHALL 能竞争成为新leader

#### Scenario: 旧leader尚未感知失锁

- **WHEN** 新leader已提交更高leader term，而旧leader的heartbeat尚未发现专用连接断开
- **THEN** 旧term SHALL 无法claim queued Run或提交checkpoint/terminal
- **AND** remote subscriber SHALL 忽略旧term迟到event

#### Scenario: leader优雅关闭

- **WHEN** execution leader收到正常shutdown信号
- **THEN** SHALL 先停止dispatch并drain或取消本地producer、停止singleton runtime
- **AND** SHALL 在上述清理完成后才释放leader lock

### Requirement: 任意 worker 创建的 Run SHALL 由 leader可靠dispatch

`POST /api/chat/runs` SHALL 在数据库事务中创建消息骨架及 `queued + owner_instance_id=NULL` 的Run，并保存经过command改写、模型解析且不含认证秘密的不可变 `launch_payload` 后快速返回；请求进程 SHALL NOT 直接启动producer。事务提交后系统 SHALL 通过所选Run bus唤醒leader；leader SHALL 同时补扫queued Run并通过当前leader term条件更新至多claim一次，只能从数据库Run、launch payload和用户记录重建执行上下文。

#### Scenario: follower创建Run

- **WHEN** 创建请求由非leader worker处理
- **THEN** 请求 SHALL 返回已持久化的同一Run身份
- **AND** leader SHALL claim并启动该Run，follower SHALL NOT 创建第二producer

#### Scenario: 创建wake-up丢失

- **WHEN** queued Run已提交但Redis wake-up未送达leader
- **THEN** leader SHALL 在有界补扫周期内发现并启动该Run
- **AND** `client_request_id`与同session active Run约束 SHALL 继续阻止重复创建

#### Scenario: claim后本地启动失败

- **WHEN** leader已将queued Run claim为running，但RunManager或Agent同步启动失败
- **THEN** leader SHALL 将Run与assistant骨架收口为明确error
- **AND** SHALL NOT 留下没有producer的running Run

#### Scenario: queued期间用户修改默认模型

- **WHEN** Run创建时已解析model identity，而用户在leader dispatch前修改默认模型
- **THEN** 该Run SHALL 继续使用launch payload中固定的model identity
- **AND** 后续新Run SHALL 使用修改后的默认值

#### Scenario: 创建用户在dispatch前失效

- **WHEN** leader无法从数据库加载launch payload对应的有效用户
- **THEN** Run与assistant骨架 SHALL 收口为明确start error
- **AND** launch payload SHALL NOT 通过保存Cookie或session对象绕过用户状态检查

### Requirement: Redis Pub/Sub SHALL 提供跨进程实时 RunEvent fan-out

execution leader SHALL 通过环境隔离、按Run标识的Redis Pub/Sub channel发布带schema version、run identity、owner identity/term、sequence、attempt和event payload的RunEvent。Web worker SHALL 在完成用户鉴权后订阅，只接受与权威Run owner term一致的event并向本进程独立SSE subscriber投递；Redis SHALL NOT 被当作Run历史或终态权威，terminal CAS loser SHALL NOT 发布自己的候选终态。

同一Run的Redis event SHALL 经单一有界publisher queue按sequence顺序发布，terminal SHALL NOT 越过尚未发布的普通event。publisher overflow或失败 SHALL 触发可观测gap恢复，不得阻塞producer或修改Run终态。

#### Scenario: SSE连接follower

- **WHEN** producer位于leader，而同一用户Tab连接follower
- **THEN** follower SHALL 通过Redis接收RunEvent并持续输出SSE
- **AND** Tab SHALL 观察到与连接leader相同的sequence和终态语义

#### Scenario: 同一worker多个Tab

- **WHEN** 一个worker上多个已鉴权Tab订阅同一远端Run
- **THEN** 该worker SHALL 只维护一份Run级Redis subscription
- **AND** 每个Tab SHALL 保持独立、有界的本地SSE queue

#### Scenario: Redis publisher变慢

- **WHEN** Redis publish速度低于producer产生event的速度并使有界publisher queue溢出
- **THEN** producer SHALL 继续按原有持久化状态机运行
- **AND** 远端subscriber SHALL 通过sequence gap和snapshot恢复，不得收到乱序terminal

### Requirement: Run bus SHALL 通过显式配置选择memory或redis adapter

系统 SHALL 要求通过 `NOESIS_RUN_BUS_BACKEND=memory|redis` 显式选择Run bus adapter。两种adapter SHALL 实现同一版本化RunEvent与wake-up port，并共用queued dispatch、durable command、checkpoint、sequence、reconciliation及SSE订阅状态机；系统 SHALL NOT 因Redis缺失或连接失败自动fallback，也 SHALL NOT 在运行中热切换。角色与instance identity SHALL 由运行时选举生成，不得人工指定leader。

#### Scenario: 单worker本地启动

- **WHEN** 开发者配置 `NOESIS_RUN_BUS_BACKEND=memory` 并只启动一个backend进程
- **THEN** 该进程 SHALL 不依赖Redis，但仍通过同一queued dispatch、Run bus、durable command和subscription路径运行
- **AND** SHALL NOT 恢复旧的请求内直接启动producer路径

#### Scenario: memory模式启动第二实例

- **WHEN** 已有backend在memory模式持有execution lock，第二个backend连接同一业务库
- **THEN** 第二个backend SHALL 在接收流量前启动失败
- **AND** SHALL 明确报告memory模式不支持多backend

#### Scenario: Redis模式配置缺失

- **WHEN** `NOESIS_RUN_BUS_BACKEND=redis` 且 `REDIS_URL` 或 `NOESIS_CLUSTER_ID` 缺失、非法或Redis启动探测失败
- **THEN** backend SHALL 在接收流量前启动失败并报告安全配置错误

#### Scenario: 模式值非法或缺失

- **WHEN** `NOESIS_RUN_BUS_BACKEND` 缺失或不是 `memory|redis`
- **THEN** backend SHALL 启动失败，不得根据 `REDIS_URL` 是否存在自动猜测或fallback

#### Scenario: 自动选举角色

- **WHEN** 多个backend使用相同PostgreSQL、Redis与cluster id启动
- **THEN** 系统 SHALL 自动选出一个leader且其余为followers
- **AND** SHALL NOT 要求或接受环境变量强制指定某实例为leader

#### Scenario: cluster id配置不一致

- **WHEN** backend连接已有runtime cluster identity的PostgreSQL，但配置了不同 `NOESIS_CLUSTER_ID`
- **THEN** backend SHALL 在订阅或发布Redis channel前启动失败
- **AND** SHALL NOT 静默形成两个互不可见的namespace

### Requirement: 远端订阅 SHALL 无窗口地建立并从 PostgreSQL恢复

worker SHALL 为同一Run共享一个Run hub：先建立所选bus subscription并缓冲事件，再读取PostgreSQL snapshot，随后按sequence去重和连续apply，并向本进程各Tab fan-out。sequence gap、bus重连或周期性对账差异 SHALL 由该hub统一触发snapshot恢复；active Run SHALL 在有未持久化sequence且无新event时按有界周期刷新checkpoint。所有握手buffer、重试和订阅资源 SHALL 有界。

本地与远端订阅 SHALL 通过同一subscription handle暴露幂等关闭语义，API层 SHALL NOT 直接依赖进程全局RunManager释放远端资源。

#### Scenario: snapshot读取窗口内发生事件

- **WHEN** follower在建立subscription与读取snapshot期间收到新RunEvent
- **THEN** 该event SHALL 先进入握手buffer
- **AND** snapshot读取后 SHALL 丢弃重复sequence并连续apply剩余event

#### Scenario: 中间Pub/Sub消息丢失

- **WHEN** subscriber的 `last_sequence=20` 而下一event sequence为23
- **THEN** subscriber SHALL NOT 直接apply sequence 23
- **AND** SHALL 等待权威snapshot追上后恢复连续订阅或有界断开重连

#### Scenario: 单个未checkpoint事件后长时间静默

- **WHEN** subscriber错过一个非语义event且producer之后长时间没有新event
- **THEN** leader SHALL 通过周期checkpoint在有界时间内持久化最新projection
- **AND** follower SHALL 能通过snapshot recovery收敛到该sequence

### Requirement: 跨进程command SHALL 持久化、幂等并由leader执行

stop、cancel与HITL resume SHALL 在PostgreSQL durable command表中完成鉴权、状态校验和幂等写入，再通过所选Run bus唤醒execution leader。stop SHALL 以Run和command type去重；HITL SHALL 以Run和interrupt identity去重并校验decision digest。leader SHALL 从数据库claim并再次校验Run/interrupt状态；bus publish成功 SHALL NOT 等价于command执行成功。API SHALL 返回 `command_id`、`command_status` 与最新Run snapshot，`accepted` SHALL 只表示命令已持久化，前端 SHALL 继续观察权威Run状态。

#### Scenario: command wake-up丢失

- **WHEN** command已提交但Redis通知丢失
- **THEN** leader SHALL 通过有界补扫发现并执行command
- **AND** 相同幂等键 SHALL NOT 产生第二次副作用

#### Scenario: command已受理但leader尚未执行

- **WHEN** API已提交durable command但有界等待内未得到leader ack
- **THEN** SHALL 以HTTP 200返回同一command且 `command_status=accepted`
- **AND** UI SHALL 保持“正在停止”或“正在继续”并订阅同一Run，不得显示操作已完成

#### Scenario: 旧HITL command迟到

- **WHEN** 已过期interrupt或旧Run的HITL resume command到达
- **THEN** leader SHALL 将其标记为rejected或no-op
- **AND** SHALL NOT 启动第二producer segment或影响后续Run

#### Scenario: 同一interrupt提交不同决策

- **WHEN** 已存在HITL command，而同一interrupt identity收到不同decision digest
- **THEN** API SHALL 返回409冲突
- **AND** SHALL NOT 覆盖或执行第二份决策

#### Scenario: queued Run在claim前停止

- **WHEN** stop command先于leader claim提交到queued Run
- **THEN** command consumer SHALL 直接收口该Run且dispatcher SHALL 无法再claim
- **AND** API确认stop后 SHALL NOT 随后出现producer启动

### Requirement: leader与Redis故障 SHALL 受控恢复

Redis不可用而PostgreSQL和Web依赖正常时，实例 SHALL 保持可路由并报告degraded，`POST /api/chat/runs` SHALL 单独拒绝新Run；已有leader Run SHALL 继续写PostgreSQL，远端subscriber SHALL 使用有界snapshot恢复。新leader获锁后 SHALL 先将旧leader已claim的非终态Run收口为 `interrupted/server_restart`，不得重放模型或工具；未被claim的queued Run SHALL 可继续dispatch。

#### Scenario: leader进程崩溃

- **WHEN** leader释放advisory lock且其它worker成为新leader
- **THEN** 新leader SHALL 至多一次收口旧leader的running/retrying/HITL Run
- **AND** 旧producer迟到checkpoint或终态 SHALL 无法覆盖数据库权威终态

#### Scenario: Redis运行中断开

- **WHEN** worker无法发布或订阅Redis
- **THEN** 健康状态 SHALL 报告Redis degraded且创建新Run SHALL 返回503
- **AND** 系统 SHALL NOT 创建第二producer或把广播失败解释为Run失败
- **AND** 已有Run查询、snapshot、stop与HITL SHALL 继续可路由

### Requirement: 分布式协调 SHALL 隔离租户并可观测

系统 SHALL 在订阅channel或创建command前完成 `(run_id,current_user_id)` 鉴权，并限制每用户、每Run和每worker的subscription、buffer、payload及pending command资源；网关 SHALL 提供部署级连接上限。进程内subscription计数 SHALL NOT 标记为部署级global指标。日志与指标 SHALL 关联 `run_id`、`instance_id`、`sequence`和`command_id`，且 SHALL NOT 记录敏感event payload。

#### Scenario: 跨用户猜测Run ID

- **WHEN** 用户请求订阅或操作不属于自己的Run
- **THEN** API SHALL 返回404
- **AND** backend SHALL NOT 建立bus subscription或写入command

#### Scenario: 多worker容量验收

- **WHEN** 至少两个worker运行规定的多Run、多Tab、慢消费与重连负载
- **THEN** 报告 SHALL 分别记录leader/follower延迟、Redis吞吐/失败、sequence gap、snapshot恢复、checkpoint lag、terminal delivery与资源回收

### Requirement: 会话与用户信令 SHALL 跨 worker 广播

会话级与用户级信令流（run-started/hitl/terminal 等 hint 事件）在 `redis` 模式下 SHALL 经 Run bus 广播到所有 worker；任一 worker 本地的信令 SSE 端点 SHALL 能投递其它进程发布的信令。信令 SHALL 保持 hint 语义（at-most-once、订阅满则丢、前端经 active-run/GET 自愈），SHALL NOT 分配 sequence、进入 checkpoint 或触发 Run 状态迁移。`memory` 模式 SHALL 保持进程内总线行为不变，信令端点代码 SHALL NOT 感知运行模式。

#### Scenario: follower 承接用户级信令流

- **WHEN** 浏览器连接非 leader worker 的 `/api/chat/events/stream`，leader 上的 Run 进入终态
- **THEN** 该 worker SHALL 向该浏览器投递 run-terminal 信令
- **AND** 会话列表徽章表现 SHALL 与直连 leader worker 一致

#### Scenario: 信令广播丢失

- **WHEN** 信令 Pub/Sub 消息丢失
- **THEN** 前端 SHALL 经既有 active-run 拉取与快照对齐自愈
- **AND** 系统 SHALL NOT 因信令丢失重置或阻塞任何 Run 状态

### Requirement: durable command SHALL 有界保留

已完成（completed/rejected/no-op）的 command SHALL 在保留期（`distributed_runs.command_retention_days`，默认 7 天）后由 leader 低频批量清理；清理 SHALL NOT 阻塞 dispatch、claim 或新 command 提交。command 保留期 SHALL 同时作为幂等去重窗口；超出窗口的重复提交 SHALL 按新 command 处理并重新校验 Run 状态。

#### Scenario: 过期 command 清理

- **WHEN** command 完成时间早于「当前时间 - 保留期」
- **THEN** leader 清理任务 SHALL 批量删除该 command
- **AND** 清理执行期间新 command 提交与 queued Run claim SHALL 不受影响

#### Scenario: 去重窗口外的重复 stop

- **WHEN** 同一 Run 的 stop command 已完成且超过保留期，再次提交 stop
- **THEN** 系统 SHALL 按新 command 处理并重验 Run 当前状态
- **AND** 已终态 Run SHALL 返回 rejected/no-op，SHALL NOT 产生第二次副作用
