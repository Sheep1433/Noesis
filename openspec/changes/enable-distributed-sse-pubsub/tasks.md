## 1. 测试基线与配置

- [x] 1.1 记录前后端、SSE专项和容量基线（收集错误已不复现：1248 collected；基线 pytest 1175 passed / load_test p50 0.126ms p99 1.175ms loop-lag 0.984ms RSS 37.4MB；P1 后 1211 passed 零新增失败、load_test 持平）
- [x] 1.2 加入异步Redis客户端依赖（redis>=5.2）；EnvSecrets必填 `NOESIS_RUN_BUS_BACKEND=memory|redis`，redis模式条件必填 `REDIS_URL`/`NOESIS_CLUSTER_ID`，`config.yaml distributed_runs`保存非敏感调优参数；禁止自动fallback、热切换和force-leader开关（模块级 DistributedRunsConfig import 即校验 fail-fast）
- [x] 1.3 为Run bus定义最小port与版本化envelope，实现memory adapter共享契约测试（test_run_bus_contract.py fixture 参数化，P4 扩 redis）；leader elector/dispatcher 以 port 注入（token_provider/bus），Service 不依赖具体 Redis client

## 2. Leader角色与多进程lifespan

- [x] 2.1 将现有advisory lock封装为leader elector（key 不变），新增含cluster identity的单行 t_runtime_leader term（migration 202608270001）；错cluster id时fail-fast（ClusterIdMismatchError）；token 失效拒绝 claim（claim 侧已接，checkpoint/terminal/Redis envelope 校验随 P4）
- [x] 2.2 使用独立migration advisory lock串行执行 `init_database()`（阻塞轮询+超时）；双worker并发验证归入 2.4 双进程测试（migration lock 语义已单测；live-PG 用例已跑绿：term 递增/跨实例锁互斥/foreign cluster fail-fast）
- [ ] 2.0 P3 动工前补充子会话分布式 spec delta：ExecutorPort 跨 loop 订阅、children/stream 目录流与子会话 stop/HITL 的分布式语义（候选：子 run 事件复用 Run bus channel 命名空间 + catalog 独立 topic；或子会话端点 leader-only + 网关亲和路由）；确定方案后修订本 change 的 spec delta 与 tasks
- [ ] 2.3 仅在leader启动/停止Run recovery、dispatcher、scheduler、memory dream、Telegram和Feishu runtime；follower不运行这些后台任务
- [ ] 2.4 增加双进程测试，覆盖redis模式leader唯一、follower ready、失锁取消、优雅关闭先drain后释放lock、重新选举和singleton runtime不重复；memory模式第二进程fail-fast

## 3. Run创建与可靠dispatch

- [x] 3.1 将 `RunService.create` 收敛为事务性创建消息骨架与queued Run（owner NULL + owner_term 0），持久化不含认证秘密的schema化 launch_payload（extra 白名单过滤 + 敏感键静态断言）；model identity 在 create 时解析冻结（resolved_model，command 改写经 notify_agent_query 仍在 producer 内）
- [x] 3.2 实现leader Run dispatcher（run_dispatcher.py）：从launch payload与数据库用户重建上下文，容量预检（满则保持queued）、wake-up 100ms 去抖 + queued补扫、claim 先提交再启动（避免行锁互等）；启动失败 RUN_START_FAILED 收口（pending stop 条件随 P2 command 落地）
- [x] 3.3 区分未claim queued Run和旧leader active Run（recovery 跳过 `queued+owner IS NULL`，owner_term >= 当前任期防御性跳过）；旧leader active Run按 `interrupted/server_restart` 收口且工具结果标unknown
- [x] 3.4 覆盖wake-up丢失（补扫兜底测试）、并发claim输家、默认模型queued期间变化（resolved_model 冻结测试）、用户失效（上下文重建失败收口测试）、leader失锁未感知（token 失效拒绝 claim 测试）、claim后崩溃（recovery 按 owner_term 收口测试）；旧term迟到写入的完整矩阵随 P4 envelope 校验

## 4. Redis RunEvent与无窗口订阅

- [ ] 4.1 实现Run bus port及memory/Redis adapter：统一envelope、订阅ack、引用计数、超时、payload上限和连接清理；Redis额外覆盖环境隔离channel与重连
- [ ] 4.2 为每个本地Run建立单consumer有界publisher queue，按owner term与sequence发布普通event和已提交terminal；CAS loser不得发布候选终态
- [ ] 4.3 定义本地/远端统一subscription handle和幂等close；同Run remote hub共享Redis订阅/握手/对账并向多Tab fan-out，API不直接调用全局RunManager
- [ ] 4.4 增加active Run周期checkpoint flush，仅在存在未持久化sequence时写入，确保长静默时snapshot有界追上
- [ ] 4.5 实现sequence gap、Redis重连和周期reconciliation；snapshot未追上时有界退避，超限只断开该subscriber并交给客户端重连
- [ ] 4.6 覆盖snapshot/subscribe竞态、单条消息丢失后长静默、重复/乱序event、慢消费、多Tab共享Redis subscription和终态通知丢失

- [ ] 4.7 会话/用户信令经Run bus广播：扩展bus port增加signal publish/subscribe（`signal:user:{user_id}` 与 `signal:session:{session_id}` 两类channel，复用envelope、不分配sequence）；redis模式follower信令SSE端点订阅远端channel并按user/session建fan-out hub（同Run hub模式）；memory模式进程内行为不变；端点代码不感知模式
- [ ] 4.8 信令广播回归：多Tab连follower时run-terminal/hitl信令投递与leader侧一致；广播丢失后前端经active-run自愈；memory模式零行为变化

## 5. Stop与HITL durable command

- [ ] 5.1 新增command model/repository/migration；stop按Run/type去重，HITL按Run/interrupt去重并保存decision digest，payload冲突返回409
- [ ] 5.2 将stop/cancel与HITL resume移到Run Service command入口，API只负责HTTP解析、认证上下文和统一响应
- [ ] 5.3 实现leader command consumer：Run bus wake-up + pending补扫；queued stop直接CAS终态并阻止dispatcher claim，active stop/HITL执行前重验状态
- [ ] 5.4 stop/HITL统一返回HTTP 200的command_id/status/latest snapshot；API提交后对command完成做有界等待（默认5s，纯读取观察不回滚command）：leader同进程常见路径返回completed，超时返回accepted；accepted不伪装完成，前端保持状态并继续订阅Run
- [ ] 5.5 覆盖wake-up丢失、重复stop、重复/过期HITL、旧Run command、leader切换和迟到ack

- [ ] 5.6 command有界保留与清理：completed/rejected/no-op command保留 `distributed_runs.command_retention_days`（默认7天，新配置项）后由leader低频批量清理；保留期=幂等去重窗口（超窗重复提交按新command重验Run状态）；清理不阻塞dispatch/claim
- [ ] 5.7 command清理回归：过期清理、清理期间新command提交、超窗重复stop对已终态Run返回rejected/no-op且无第二次副作用

## 6. 故障、鉴权与可观测性

- [ ] 6.1 拆分liveness/readiness/degraded状态并报告实际adapter；redis模式运行期不可用时Web仍可路由且仅创建Run返回503，已有Run查询/snapshot/stop/HITL继续可用；memory模式不探测Redis
- [ ] 6.2 保证所有订阅和command在建立bus资源前完成 `(run_id,current_user_id)` 鉴权，跨用户统一404
- [ ] 6.3 增加leader/dispatch、local/remote subscription、Redis、握手buffer、gap/reconciliation、周期checkpoint、command和event-to-client指标与结构化日志
- [ ] 6.4 将subscription配额定义为worker本地硬上限，并在网关增加部署级连接上限；按副本数验证最坏总连接数，不使用易泄漏的Redis精确计数
- [ ] 6.5 自动化故障矩阵：Redis启动失败/运行中重启、PostgreSQL短断、leader kill、旧leader迟到事件、Pub/Sub丢消息、command积压和滚动发布

## 7. 部署与文档

- [ ] 7.1 更新dev/prod脚本、Compose、env模板和部署文档：通过 `NOESIS_RUN_BUS_BACKEND` 显式选择；dev可注入memory，分布式prod注入redis、启动/检查Redis与cluster id；CLI参数仅映射同一变量
- [ ] 7.2 更新Nginx/upstream与测试路由，使E2E可固定连接leader或follower且SSE保持禁缓冲
- [ ] 7.3 删除请求worker直接start producer及被新架构替代的分支/测试；redis模式将第二backend变为follower，memory模式保留明确的第二backend fail-fast，不保留业务状态机开关
- [ ] 7.4 实现完成后更新 `docs/architecture/platform/chat-streaming.md`、`durable-agent-runs.md` 与release runbook为当前架构

## 8. 验收

- [ ] 8.1 后端全量测试通过，并执行memory/Redis共享契约、真实PostgreSQL、真实Redis和双backend集成测试；核心场景不得mock掉进程边界
- [ ] 8.2 前端test/lint/build与真实双backend Playwright E2E全部通过，跨worker多Tab、重连、stop和HITL场景不得skip
- [ ] 8.3 以一个leader、至少一个follower执行100 active Run、每Run 2–3 Tab、每Run 10–30 events/s容量测试，记录leader/follower p50/p95/p99、Redis吞吐、event-loop lag、RSS、queue/checkpoint lag、gap恢复和terminal delivery
- [ ] 8.4 在staging执行Redis重启、leader崩溃/重选、滚动发布、跨worker stop/HITL与回滚演练，并将命令和结果写入release runbook
- [ ] 8.5 运行 `openspec validate enable-distributed-sse-pubsub --strict`，再按原始Spec与项目规范执行code review；仅对review确认的复杂代码使用code simplification
- [ ] 8.6 CI workflow增加真实Redis service：contract参数化用例与双backend集成测试在CI可跑（非仅本地），memory模式用例不依赖Redis服务
