## 1. 测试基线与配置

- [ ] 1.1 修复当前后端全量测试收集错误，记录前后端、SSE专项和容量基线，后续每阶段均不得引入新增失败
- [ ] 1.2 加入异步Redis客户端依赖；EnvSecrets必填 `NOESIS_RUN_BUS_BACKEND=memory|redis`，redis模式条件必填 `REDIS_URL`/`NOESIS_CLUSTER_ID`，`config.yaml distributed_runs`保存非敏感调优参数；禁止自动fallback、热切换和force-leader开关
- [ ] 1.3 为Run bus、leader elector和Run dispatcher定义最小port与版本化envelope，实现memory/Redis adapter共享契约测试，Service不得依赖具体Redis client

## 2. Leader角色与多进程lifespan

- [ ] 2.1 将现有advisory lock封装为leader elector，新增含cluster identity的单行runtime leader term；claim/checkpoint/terminal/Redis envelope校验term，错cluster id时fail-fast
- [ ] 2.2 使用独立migration advisory lock串行执行 `init_database()`，验证两个worker并发启动不会重复migration或提前recovery
- [ ] 2.3 仅在leader启动/停止Run recovery、dispatcher、scheduler、memory dream、Telegram和Feishu runtime；follower不运行这些后台任务
- [ ] 2.4 增加双进程测试，覆盖redis模式leader唯一、follower ready、失锁取消、优雅关闭先drain后释放lock、重新选举和singleton runtime不重复；memory模式第二进程fail-fast

## 3. Run创建与可靠dispatch

- [ ] 3.1 将 `RunService.create` 收敛为事务性创建消息骨架与queued Run，并持久化command改写后、固定model identity且不含认证秘密的schema化launch payload
- [ ] 3.2 实现leader Run dispatcher：从launch payload与数据库用户重建上下文，容量预检、wake-up、queued补扫及leader term/pending stop条件claim；启动失败终态收口
- [ ] 3.3 区分未claim queued Run和旧leader active Run；新leader只继续前者，后者按 `interrupted/server_restart` 收口且工具结果标unknown
- [ ] 3.4 覆盖创建ACK/wake-up丢失、并发创建、默认模型在queued期间变化、用户失效、leader失锁未感知、claim前后崩溃和旧term迟到写入

## 4. Redis RunEvent与无窗口订阅

- [ ] 4.1 实现Run bus port及memory/Redis adapter：统一envelope、订阅ack、引用计数、超时、payload上限和连接清理；Redis额外覆盖环境隔离channel与重连
- [ ] 4.2 为每个本地Run建立单consumer有界publisher queue，按owner term与sequence发布普通event和已提交terminal；CAS loser不得发布候选终态
- [ ] 4.3 定义本地/远端统一subscription handle和幂等close；同Run remote hub共享Redis订阅/握手/对账并向多Tab fan-out，API不直接调用全局RunManager
- [ ] 4.4 增加active Run周期checkpoint flush，仅在存在未持久化sequence时写入，确保长静默时snapshot有界追上
- [ ] 4.5 实现sequence gap、Redis重连和周期reconciliation；snapshot未追上时有界退避，超限只断开该subscriber并交给客户端重连
- [ ] 4.6 覆盖snapshot/subscribe竞态、单条消息丢失后长静默、重复/乱序event、慢消费、多Tab共享Redis subscription和终态通知丢失

## 5. Stop与HITL durable command

- [ ] 5.1 新增command model/repository/migration；stop按Run/type去重，HITL按Run/interrupt去重并保存decision digest，payload冲突返回409
- [ ] 5.2 将stop/cancel与HITL resume移到Run Service command入口，API只负责HTTP解析、认证上下文和统一响应
- [ ] 5.3 实现leader command consumer：Run bus wake-up + pending补扫；queued stop直接CAS终态并阻止dispatcher claim，active stop/HITL执行前重验状态
- [ ] 5.4 stop/HITL统一返回HTTP 200的command_id/status/latest snapshot；accepted不伪装完成，前端保持状态并继续订阅Run
- [ ] 5.5 覆盖wake-up丢失、重复stop、重复/过期HITL、旧Run command、leader切换和迟到ack

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
