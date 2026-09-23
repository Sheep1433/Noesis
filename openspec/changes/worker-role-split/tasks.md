# Tasks: 进程角色三分（web / control / worker）

## 1. 数据层：认领 epoch 与心跳（Phase 1，可独立验收）

- [x] 1.1 `storage/postgres/models/chat.py`：`TAgentRun` 新增 `claim_epoch`（Integer, default 0）、`heartbeat_at`（BigInteger, nullable）；`owner_term` 注释更新为审计字段（不参与 fencing）
- [x] 1.2 Alembic migration：纯加列（`claim_epoch` server_default '0'、`heartbeat_at` nullable），存量行零回填；验证现有库升级路径
- [x] 1.3 `repositories/agent_run_repository.py`：`claim_queued` 升级——CAS 条件加 `heartbeat 超时或为空`，写入 `claim_epoch = claim_epoch + 1`；新增 `heartbeat(run_id, now_ms)`（带 `WHERE claim_epoch = :epoch` 条件）；`save_checkpoint` / 终态 CAS 的 UPDATE 追加 `claim_epoch` 条件（僵尸写 DB 层拒绝）
- [x] 1.4 单测：epoch 递增幂等；**epoch 单调性**（多轮重置-再认领后 epoch 严格递增，旧 epoch 永不等于当前值）；心跳条件更新只命中本 epoch；僵尸 epoch 的 checkpoint 写被拒（rowcount=0）；claim 只认领 `queued AND owner IS NULL`（heartbeat 超时行不被 claim 直取，须经对账）

## 2. 对账阶段化（Phase 1）

- [x] 2.1 `services/run_recovery_service.py`：僵尸判定改「heartbeat 超时」+ 阶段化分流——`last_sequence=0 且 snapshot IS NULL 且 launch_payload 非空` → 重置 queued + 清 `owner_instance_id` 与 `heartbeat_at`（**claim_epoch 保留递增不归零**——归零会制造 epoch 碰撞，第一代超长假死僵尸可通过校验）；否则收口 interrupted（保留现有部分成果语义）
- [x] 2.2 `server/bootstrap/leader_runtime.py`：阶段化收敛在 `recover_orphaned_runs` 内部实现，对账步骤清单对外接口未变（`_main_runs` 调用不变、顺序语义不变）——`test_leader_runtime_order.py` 全绿无需改写
- [x] 2.3 单测：未碰世界 run 重置后被再认领执行；已碰世界 run 收口 interrupted 且部分成果保留；heartbeat 未超时的 run 不被对账触碰

## 3. 认领循环 worker 化 + fencing 管道（Phase 2）

- [x] 3.1 `services/run_dispatcher.py`：去 leader 化——`token_provider` 校验移除，认领者标识改为进程实例 ID；容量判定从全局 run_manager 容量改 per-process 配额；bus wake-up + 补扫机制原样保留
- [x] 3.2 `chat/runs/manager.py` + `chat/runs/publisher.py`：term/token 逐操作校验替换为 per-run `claim_epoch`——RunHandle 持有认领时 epoch，`apply_event` 拒绝路径（现 `StaleProducerGeneration`）与 publisher 提交前校验改比对 epoch
- [x] 3.3 SKIP LOCKED 批量圈行（多 worker 同批 queued 的竞争优化）：`claim_next_batch(limit)` —— `SELECT ... FOR UPDATE SKIP LOCKED` 圈行后逐行 CAS；单 worker 部署路径走原单行 CAS 不变
- [x] 3.4 worker 心跳协程：持有期间周期 `heartbeat()`（间隔 lease_ttl/3），随 run 终态/释放停止
- [x] 3.5 单测：双模拟 worker 并发认领同批 run 无双跑；epoch 不符事件被拒；心跳随生命周期启停

## 4. 命令消费分片（Phase 2）

- [x] 4.1 `services/run_command_service.py`：消费扫描加 run 归属过滤——命令 `run_id` 在本进程 handle 注册表命中才认领；未命中跳过（留给 owner worker 或对账重置）；无 run 归属的全局命令路由 control；认领租约机制原样
- [x] 4.2 单测：本进程命令消费、他进程命令跳过；owner 崩溃后命令经租约超时重置被对账/新 owner 消费

## 5. 三入口拆分（Phase 3）

- [x] 5.1 `app.py` 重组为公共装配库（lifespan 拆分为 web/control/worker 三个装配函数）；新增 `web.py` / `control.py` / `worker.py` 三入口（uvicorn 直挂各自 app）
- [x] 5.2 测试基建迁移：`server.main` 转为 web app 兼容导出层（`app = build_web_app()`），全部 TestClient/挂载守卫测试零改动通过
- [x] 5.3 control 经 `pg_manager.acquire_advisory_lock` 防双开（fail-fast）；web/worker 无锁启动；LeaderElector 的 redis 选主分支不再被任何入口引用（文件清理列入 7.x 后续）
- [x] 5.4 启动校验：`worker > 1 且 NOESIS_RUN_BUS_BACKEND != redis` → fail-fast；`/health` 上报 `role` 与各面状态
- [x] 5.5 `deploy/docker-compose.yml`：三 service（同镜像不同 command）；`scripts/run.sh` dev 模式起三进程；部署文档更新（含旧单容器形态的迁移说明）
- [x] 5.6 沙箱装配归属 worker：`ensure_sandbox_runner_process` 拉起与 `shutdown_sandboxes` 随 worker lifespan；子代理执行器/隔离循环装配同归（follower 分支"不运行执行面"语义自然继承）

## 6. 回归与验收

- [x] 6.1 单测全绿 + `tests/api_contract` + `test_doc_contract` 契约门禁
- [ ] 6.2 集成（真库，待环境）：双 worker 并发认领无双跑；worker kill -9 → lease_ttl 内对账收口；重置 run 被另一 worker 续跑到终态；停止/HITL 命令经分片消费生效
- [ ] 6.3 三进程手动验收（待环境）：单 run 双标签页 SSE 一致；断线重连补发；定时任务经 control 触发；通道（Telegram）消息经 control 消费；**双 web 进程下信令消费向**（跨窗口实时刷新）
- [x] 6.4 文档：AGENTS/README 启动命令同步；决策记录 `2026-09-23-进程角色三分worker-role-split.md`（chat-streaming.md 部署节归档时一并）

## 7. 后续（本变更不含）

- [ ] 7.1 `owner_term` 字段退役评估（审计价值 vs 维护成本）
- [ ] 7.2 control 单点的认领原语泛化（调度任务/通道轮询/记忆扫描行化）
- [ ] 7.3 子代理执行器侧 encode-once 同模式改造（沿用 encode-once-fanout-bytes 后续项）
- [ ] 7.4 `LeaderElector` 退役文件清理（redis 选主分支 / `RuntimeLeaderRepository` term 提交，入口已不引用）
