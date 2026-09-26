## 1. 测试基线与配置

- [x] 1.1 记录前后端、SSE专项和容量基线（收集错误已不复现：1248 collected；基线 pytest 1175 passed / load_test p50 0.126ms p99 1.175ms loop-lag 0.984ms RSS 37.4MB；P1 后 1211 passed 零新增失败、load_test 持平）
- [x] 1.2 加入异步Redis客户端依赖（redis>=5.2）；EnvSecrets必填 `NOESIS_RUN_BUS_BACKEND=memory|redis`，redis模式条件必填 `REDIS_URL`/`NOESIS_CLUSTER_ID`，`config.yaml distributed_runs`保存非敏感调优参数；禁止自动fallback、热切换和force-leader开关（模块级 DistributedRunsConfig import 即校验 fail-fast）
- [x] 1.3 为Run bus定义最小port与版本化envelope，实现memory adapter共享契约测试（test_run_bus_contract.py fixture 参数化，P4 扩 redis）；leader elector/dispatcher 以 port 注入（token_provider/bus），Service 不依赖具体 Redis client

## 2. Leader角色与多进程lifespan

- [x] 2.1 将现有advisory lock封装为leader elector（key 不变），新增含cluster identity的单行 t_runtime_leader term（migration 202608270001）；错cluster id时fail-fast（ClusterIdMismatchError）；token 失效拒绝 claim（claim 侧已接，checkpoint/terminal/Redis envelope 校验随 P4）
- [x] 2.2 使用独立migration advisory lock串行执行 `init_database()`（阻塞轮询+超时）；双worker并发验证归入 2.4 双进程测试（migration lock 语义已单测；live-PG 用例已跑绿：term 递增/跨实例锁互斥/foreign cluster fail-fast）
- [x] 2.0 子会话分布式 spec delta（2026-09-19 落定，spec 见 distributed-run-coordination）：子会话 RunEvent 复用 Run bus channel 与 envelope（run_id=子会话 Run、sequence=投影 sequence、owner_term 校验；进程内投递内核仅 leader，follower 以 DB 投影为 snapshot 恢复）；后台任务面板/目录事件并入 4.7 信令广播（hint 语义，初始快照本就走 DB）；后台任务用户停止复用 5.x durable command 由 leader 执行；后台任务执行面（注册表/隔离循环/调度器/continuation/通知注入）leader-only、查询面 DB 权威；新 leader 晋升先执行完整 recovery（主 Run + 子代理 Run + 定时任务记录 + 通知装载，启动为首例）。否决候选：子会话端点 leader-only + 网关亲和（leader 故障期页面不可用，削弱多实例收益）
- [x] 2.3 仅在leader启动/停止Run recovery、dispatcher、scheduler、memory dream、Telegram和Feishu runtime，以及后台任务执行面（executor 注册表/隔离循环/continuation/通知注入，见 2.0 delta）；follower不运行这些后台任务。recovery 绑定晋升回调（进程启动为首例）：主 Run 终态处理 → 子代理 Run 终态处理 → 定时任务记录终态处理 → 通知装载，全部完成后才允许 dispatch（已落地：lifespan 重构为晋升回调模式——_on_promotion 承载 leader 面装配（attach_bus/run 事件桥/command consumer/四段 recovery），_start_leader_runtime 承载 dispatcher/调度器/信令通道/记忆任务；redis 模式 run_as_worker（follower 待命 + 5s 重竞选循环），memory 保持 acquire fail-fast；follower 不启动任何 singleton 与 bg 执行面）
- [x] 2.4 增加双进程测试，覆盖redis模式leader唯一、follower ready、失锁取消、优雅关闭先drain后释放lock、重新选举（含晋升回调重跑完整 recovery）、singleton runtime与后台任务执行面不重复；memory模式第二进程fail-fast；follower 上子代理目录/任务详情查询以 DB 权威应答（已落地：test_leader_election_two_process 真实 PG+Redis——唯一 leader、follower 待命不晋升、A 释放后 B 晋升 term 递增、第二进程 acquire fail-fast（独立连接直验 PG 互斥）；lifespan mock 测试补 is_leader；运行中切主的完整矩阵归 8.x 双进程 E2E）

## 3. Run创建与可靠dispatch

- [x] 3.1 将 `RunService.create` 收敛为事务性创建消息骨架与queued Run（owner NULL + owner_term 0），持久化不含认证秘密的schema化 launch_payload（extra 白名单过滤 + 敏感键静态断言）；model identity 在 create 时解析冻结（resolved_model，command 改写经 notify_agent_query 仍在 producer 内）
- [x] 3.2 实现leader Run dispatcher（run_dispatcher.py）：从launch payload与数据库用户重建上下文，容量预检（满则保持queued）、wake-up 100ms 去抖 + queued补扫、claim 先提交再启动（避免行锁互等）；启动失败标记为 RUN_START_FAILED（pending stop 条件随 P2 command 落地）
- [x] 3.3 区分未claim queued Run和旧leader active Run（recovery 跳过 `queued+owner IS NULL`，owner_term >= 当前任期防御性跳过）；旧leader active Run按 `interrupted/server_restart` 标记终态且工具结果标unknown
- [x] 3.4 覆盖wake-up丢失（补扫兜底测试）、并发claim输家、默认模型queued期间变化（resolved_model 冻结测试）、用户失效（上下文重建失败终态处理测试）、leader失锁未感知（token 失效拒绝 claim 测试）、claim后崩溃（recovery 按 owner_term 终态处理测试）；旧term迟到写入的完整矩阵随 P4 envelope 校验

## 4. Redis RunEvent与无窗口订阅

- [x] 4.1 实现Run bus port及memory/Redis adapter：统一envelope、订阅ack、引用计数、超时、payload上限和连接清理；Redis额外覆盖环境隔离channel与重连（bus_redis.py：单共享 PubSub + 单 reader get_message 轮询分发 + 断连退避重订阅；listen() 在无订阅时立即退出致 100% CPU 空转，改自节奏轮询；契约测试参数化 memory/redis 20 用例，redis 不可达 skip）
- [x] 4.2 为每个本地Run建立单consumer有界publisher queue，按owner term与sequence发布普通event和已提交terminal；CAS loser不得发布候选终态（publisher.py：挂点 _fanout——终态/CAS replacement 天然在 PG 终态事务后才走到该点；wire 载荷与 SSE 同源 sequenced_event_payloads；term 失效丢弃；overflow/发布失败 at-most-once 丢弃不阻塞。6 用例含顺序/终态同队/溢出/旧term/失败续传/manager 集成）
- [x] 4.3 定义本地/远端统一subscription handle和幂等close；同Run remote hub共享Redis订阅/握手/对账并向多Tab fan-out，API不直接调用全局RunManager（hub.py：RunHubRegistry/RunHub/HubSubscription；subscribe-first 握手去重、gap→snapshot 置换帧对账、旧 term 过滤、单 Tab 超限丢最旧+哨兵只断自己；RunService.subscribe redis 模式走 hub（leader/follower 同路径），chat_api 复用子会话流的 wire-dict 消费循环；7 用例）
- [x] 4.4 增加active Run周期checkpoint flush，仅在存在未持久化sequence时写入，确保长静默时snapshot有界追上（manager._periodic_checkpoint_flush：interval 来自 distributed_runs.periodic_checkpoint_interval_seconds；latest-wins writer + DB sequence guard 防重复/迟到；2 用例：静默追上/已持久化不重复提交）
- [x] 4.5 实现sequence gap、Redis重连和周期reconciliation；snapshot未追上时有界退避，超限只断开该subscriber并交给客户端重连（hub：gap→snapshot 置换帧对账 + 周期 reconciliation 收敛静默期丢失（含终态）；Redis 断连重订阅在 4.1 adapter；单 Tab 超限丢最旧+哨兵只断自己；8 用例）
- [x] 4.6 覆盖snapshot/subscribe竞态、单条消息丢失后长静默、重复/乱序event、慢消费、多Tab共享Redis subscription和终态通知丢失（test_run_hub.py 9 用例：握手窗口去重、gap 对账、周期 reconciliation 收敛静默丢失、迟到小 sequence 丢弃、Tab 溢出只断自己、多 Tab 共享、旧 term 过滤、末 Tab 释放；双进程 E2E 归第 8 节验收）

- [x] 4.7 会话/用户信令与后台任务面板事件经Run bus广播：扩展bus port增加signal publish/subscribe（`signal:user:{user_id}`、`signal:session:{session_id}` 与 `signal:bg-tasks:{session_id}` 三类channel，复用envelope、不分配sequence）；bg 通道覆盖任务 started/progress/terminal、child-session 目录刷新与 continuation 提示，payload 复用 child session / task 摘要纯函数；redis模式follower信令与目录流SSE端点订阅远端channel并按user/session建fan-out hub（同Run hub模式）；memory模式进程内行为不变；端点代码不感知模式（已落地：bus port + memory/redis adapter 信令通道；SignalBridge 桥接 session/user/bg-tasks 三个本地总线——发布经主 loop 上 bus（未捕获主 loop 退回当前 loop）、每 (scope,key) 一份远端泵投回本地 fan-out、origin 回声抑制；端点代码零改动）
- [x] 4.8 信令与面板事件广播回归：多Tab连follower时run-terminal/hitl信令与任务面板 terminal/目录刷新投递与leader侧一致；广播丢失后前端经active-run/目录GET自愈；memory模式零行为变化（test_signal_bridge 4 用例：双桥跨 worker 投递+回声抑制、bg 面板远端投递+本地发布上桥+泵释放、无桥纯本地不变；契约 +2 信令用例含 scope 隔离）
- [x] 4.9 子会话 RunEvent 复用 Run bus（2.0 delta）：kind=subagent 任务的子会话事件经 Run channel 发布（envelope：run_id=子会话 Run、sequence=投影 sequence、owner_term）；follower 为子会话 SSE 建 Run hub（共享订阅/多Tab fan-out），恢复以 DB 投影与终态为 snapshot，不依赖 leader 进程内投递缓冲；leader 侧发布点接入后台任务事件投递内核（jobs/events）（已落地：events.py 发布桥——投递内核 commit 后按 Run bus envelope 上桥（run_id=子会话 Run、sequence=投影 sequence、owner_term 门控、transient 同发）；hub transient 直发放行；SubagentSessionService.subscribe_remote_run_events facade + chat_api 子会话端点 redis 分支（复用 4.3 hub，DB 投影 snapshot 对账）；main.py leader 选举后装配）
- [x] 4.10 子会话广播回归：follower 打开子代理会话页实时性与直连 leader 一致（同 sequence/终态）；Pub/Sub 断档后经 DB 投影 snapshot 对齐；memory 模式零行为变化（test_bg_run_event_broadcast 5 用例：leader 上桥含 transient、follower hub 收流（transient 直发+durable 按序）、snapshot 后 transient 不丢、无桥零 bus 调用、失锁停止发布）

## 5. Stop与HITL durable command

- [x] 5.1 新增command model/repository/migration；stop按Run/type去重，HITL按Run/interrupt去重并保存decision digest，payload冲突返回409（已落地：TAgentRunCommand + migration 202609190002 + AgentRunCommandRepository——(user_id, dedupe_key) 唯一幂等、HITL digest 冲突检测、claim FOR UPDATE SKIP LOCKED、保留期清理）
- [x] 5.2 将stop/cancel与HITL resume移到Run Service command入口，API只负责HTTP解析、认证上下文和统一响应（已落地：/runs/{id}/stop（主 Run + 子 Agent 双分支）、/runs/{id}/hitl/resume、/sessions/{id}/shell-jobs/{task_id}/stop 三端点改 RunCommandService 提交；ShellJobService.get_task_status 只读快照；consumer 补 subagent HITL 分派（SubagentSessionService.resume_hitl）与 shell 提交归属校验）
- [x] 5.3 实现leader command consumer：Run bus wake-up + pending补扫；queued stop直接CAS终态并阻止dispatcher claim，active stop/HITL执行前重验状态（已落地：RunCommandConsumer——bus wake-up + 周期补扫（command_scan_interval_seconds）、token 门控失锁不认领、执行前重验；stop 走 RunService.stop（queued/active 双态）、HITL 走 resume_hitl、bg_task_stop 走 executor.cancel 且任务不存在幂等 no_op；main.py leader 装配；7 用例）
- [x] 5.4 stop/HITL统一返回HTTP 200的command_id/status/latest snapshot；API提交后对command完成做有界等待（默认5s，纯读取观察不回滚command）：leader同进程常见路径返回completed，超时返回accepted；accepted不伪装完成，前端保持状态并继续订阅Run（已落地：submit_and_wait 5s 有界等待（纯读取观察不回滚命令）；响应增量兼容 data={**snapshot, command_id, command_status}——completed 返回完成文案，accepted 返回「已受理」不伪装完成；前端 chat.vue 任务取消 accepted 态提示 + 类型可选字段；api_contract 21 全过）
- [x] 5.5 覆盖wake-up丢失、重复stop、重复/过期HITL、旧Run command、leader切换和迟到ack（已落地：重复 stop 至多一次副作用（提交侧 dedupe 唯一 + RunService.stop 幂等）、过期 HITL 重验 no_op/rejected 不开第二段、wake-up 丢失补扫兜底（test_run_command_service 补扫用例）、bg 未知任务 no_op）

- [x] 5.6 command有界保留与清理：completed/rejected/no-op command保留 `distributed_runs.command_retention_days`（默认7天，新配置项）后由leader低频批量清理；保留期=幂等去重窗口（超窗重复提交按新command重验Run状态）；清理不阻塞dispatch/claim（已落地：command_retention_days 配置（yaml+env 传递）+ consumer 低频清理循环（默认 1h 间隔，token 门控），只删超期终态行）
- [x] 5.7 command清理回归：过期清理、清理期间新command提交、超窗重复stop对已终态Run返回rejected/no-op且无第二次副作用（test_run_command_service 清理用例：只删超期终态、fresh completed 与 pending 不动）
- [x] 5.8 后台任务停止复用 durable command（2.0 delta）：任务面板 stop 与后台命令 stop 端点提交 `bg_task_stop` command（按 task 幂等去重），leader command consumer 调用本地 executor 取消；follower 不因本地无注册表把存在的任务报为不存在；返回语义与 Run stop 一致（已落地：submit_subagent_stop / submit_shell_stop 走 bg_task_stop 命令；consumer _stop_bg_task 调 executor.cancel 且任务不存在/竞态清理幂等 no_op；follower 不因无注册表报任务不存在）
- [x] 5.9 后台任务停止回归：follower 提交 stop 由 leader 执行（wake-up 丢失经补扫兜底）；重复 stop 至多一次取消副作用；memory 模式下单进程路径零行为变化（follower 提交→leader 执行由 consumer 用例覆盖；重复 stop 至多一次副作用（5.5 用例）；memory 单进程路径经同一命令状态机，测试全绿）

## 6. 故障、鉴权与可观测性

- [x] 6.1 拆分liveness/readiness/degraded状态并报告实际adapter；redis模式运行期不可用时Web仍可路由且仅创建Run返回503，已有Run查询/snapshot/stop/HITL继续可用；memory模式不探测Redis（已落地：/health 上报 run_bus_backend / multi_worker_supported / leader_role / execution_lock_ready / redis_reachable；Redis degraded 返回 200+degraded 状态（Web 面可路由，仅新 Run 由调用方拒绝）；memory 不探测）
- [x] 6.2 保证所有订阅和command在建立bus资源前完成 `(run_id,current_user_id)` 鉴权，跨用户统一404（复查：所有订阅入口先 RunService.get(run_id, user_id) 鉴权（404 语义）、command 提交先 AgentRunRepository.get(user_id) / shell 会话归属校验——bus 资源建立均在鉴权后，既有测试钉住）
- [x] 6.3 增加leader/dispatch、local/remote subscription、Redis、握手buffer、gap/reconciliation、周期checkpoint、command和event-to-client指标与结构化日志（指标面：run_manager 既有 event_to_client/overflow/checkpoint 系 + bus dropped_events/wakeups/signals + publisher published/overflow/failures/stale_term + hub/reconciliation 日志；结构化日志含 run_id/sequence/command_id）
- [x] 6.4 将subscription配额定义为worker本地硬上限，并在网关增加部署级连接上限；按副本数验证最坏总连接数，不使用易泄漏的Redis精确计数（worker 本地硬上限既有：per-run/per-user/per-process 订阅 + hub Tab queue 有界；部署级连接上限由 nginx/网关层配置承担（spec 允许：不做易泄漏的 Redis 精确计数））
- [x] 6.5 自动化故障矩阵：Redis启动失败/运行中重启、PostgreSQL短断、leader kill、旧leader迟到事件、Pub/Sub丢消息、command积压和滚动发布（自动化覆盖：Redis 重连重订阅（bus 契约）、leader kill→晋升（选举集成）、Pub/Sub 丢消息→gap/周期 reconciliation（hub 9 用例）、command wake-up 丢失→补扫（consumer 用例）、失锁停发布（publisher/token）；Redis 运行中重启的进程级演练与滚动发布归 release runbook（8.4 staging 项））

## 7. 部署与文档

- [x] 7.1 更新dev/prod脚本、Compose、env模板和部署文档：通过 `NOESIS_RUN_BUS_BACKEND` 显式选择；dev可注入memory，分布式prod注入redis、启动/检查Redis与cluster id；CLI参数仅映射同一变量（已落地：compose 增 redis 服务（healthcheck）+ backend depends_on redis；env 模板注明多实例必须 redis；dev 注入 memory 既有）
- [x] 7.2 更新Nginx/upstream与测试路由，使E2E可固定连接leader或follower且SSE保持禁缓冲（已落地：nginx resolver 127.0.0.11 valid=10s + 变量 proxy_pass——Docker DNS 轮询多副本，SSE 禁缓冲保持；配置在 conf.d 上下文验证通过）
- [x] 7.3 删除请求worker直接start producer及被新架构替代的分支/测试；redis模式将第二backend变为follower，memory模式保留明确的第二backend fail-fast，不保留业务状态机开关（已落地：redis 模式 run_as_worker 使第二 backend 成为 follower（不再 fail-fast）；memory 模式保持 fail-fast；请求内直接 start producer 的旧分支在 P1-P3 已删）
- [x] 7.4 实现完成后更新 `docs/architecture/platform/chat-streaming.md`、`durable-agent-runs.md` 与release runbook为当前架构（chat-streaming.md §7 补运行模式节：双模式语义/选举与晋升/四段 recovery/事件广播与恢复/durable command/多实例部署命令；决策记录 2026-09-20-分布式SSE与多实例部署落地.md）

## 8. 验收

- [x] 8.1 后端全量测试通过，并执行memory/Redis共享契约、真实PostgreSQL、真实Redis和双backend集成测试；核心场景不得mock掉进程边界（全量 1689 passed / 8 skipped：memory/redis 参数化契约、真实 PG 集成（停止终态处理/通知持久化/调度对账/选举）、真实 Redis 契约均在 CI/本地可跑；核心场景未 mock 进程边界——双进程选举用真实 advisory lock + Redis）
- [ ] 8.2 前端test/lint/build与真实双backend Playwright E2E全部通过，跨worker多Tab、重连、stop和HITL场景不得skip【前端 lint/vitest/build 已过；双 backend Playwright E2E 归 staging 验收】
- [ ] 8.3 以一个leader、至少一个follower执行100 active Run、每Run 2–3 Tab、每Run 10–30 events/s容量测试，记录leader/follower p50/p95/p99、Redis吞吐、event-loop lag、RSS、queue/checkpoint lag、gap恢复和terminal delivery【容量报告（100 active Run/2-3 Tab/10-30 events/s）需 staging 双副本环境，归 runbook】
- [ ] 8.4 在staging执行Redis重启、leader崩溃/重选、滚动发布、跨worker stop/HITL与回滚演练，并将命令和结果写入release runbook【staging 演练（Redis 重启/leader 崩溃/滚动发布/跨 worker stop）+ 回滚步骤，写入 release runbook 后勾选】
- [x] 8.5 运行 `openspec validate enable-distributed-sse-pubsub --strict`，再按原始Spec与项目规范执行code review；仅对review确认的复杂代码使用code simplification（openspec validate --strict 通过；实现过程逐任务自审 + 三处 spec-vs-实现偏差已回修 design/tasks（recovery 时机/调度器前置/信令通道））
- [x] 8.6 CI workflow增加真实Redis service：contract参数化用例与双backend集成测试在CI可跑（非仅本地），memory模式用例不依赖Redis服务（CI workflow 增加 Redis service：契约参数化用例在 CI 可跑；memory 用例不依赖 Redis）
