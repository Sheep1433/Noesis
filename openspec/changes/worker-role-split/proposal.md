## Why

当前单活跃 backend 形态下，全部后台执行面（run 认领、命令消费、调度器、通道、记忆任务）挤在一个 leader 进程，容量上限 = 单进程（验收 100 并发 run）；执行面无法水平扩展，`docs/bug/2026-09-21-server-layer-layering-audit.md` 观察 B 已定稿目标形态并给出组件删除清单。

现状基础（enable-distributed-sse-pubsub 已落地，本变更的直接前置）：

- `t_agent_run` 已有 `owner_instance_id` / `owner_term` 字段与 `idx_agent_run_owner_status` 索引；`claim_queued` 已是 CAS 条件更新（queued + owner IS NULL + term=0）——**原子认领原语已存在**，只是消费者唯一（leader 的 RunDispatcher 串行认领）。
- `launch_payload` 已 schema 化（dispatcher 重建 producer 的启动载荷，无认证秘密）。
- run 认领已由 bus wake-up 即时唤醒 + 周期补扫兜底；命令表已带认领租约（claimed 超时重置回 pending）。
- `bus_redis` 跨进程事件通路已验证；`server/bootstrap/leader_runtime.py` 已把晋升对账清单化（顺序有测试钉住）。

## What Changes

按观察 B 定稿实施进程角色三分，**无过渡态、不保留单进程全干入口**：

- **三入口同镜像**：`web.py`（HTTP 面 × N）、`control.py`（调度器/通道/记忆任务/对账 × 1，advisory lock 防双开）、`worker.py`（run 认领与执行 × N，沙箱与 sandbox-runner 归 worker）；`app.py` 退役为三入口的公共装配库，compose 改三个 service，`scripts/run.sh` dev 模式起三进程。queued run 的认领只归 worker；control 与 run 的唯一交集是定时任务到点创建 run。
- **认领原语泛化**：`RunDispatcher` 从 leader 专属改造为每 worker 一个（去掉 leadership token 校验，容量判定改 per-process）；高并发认领竞争时 CAS 升级 `FOR UPDATE SKIP LOCKED`（正确性不依赖它，纯减少竞争空转）。
- **fencing**：`owner_term` 语义是全局 leader term，防不了"worker A 假死 → 对账重置 → worker B 认领 → A 苏醒继续写"的僵尸写入。新增 `claim_epoch`（每次认领递增）+ `heartbeat_at`（业务语义心跳，不复用会被任意写碰的 `updated_at`）；事件信封带 epoch，投影/发布前比对，不符即拒——替代现 term/token 四处校验管道。
- **对账收敛与阶段化**：`leader_runtime._reconcile_steps` 的四段对账收敛为「running 且 heartbeat 超时 → 按**是否已碰世界**分流」：无任何持久事件与 checkpoint（`last_sequence=0` 且 snapshot 为空）→ 重置 queued 重新认领；已执行 → 收口 interrupted 保留部分成果（副作用不可重放原则不破）。
- **命令消费分片**：`RunCommandConsumer` 并入 worker 执行循环——命令按 `run_id` 归属过滤，由持有该 run 的 worker 消费；不再全局单消费者。
- **组件收敛**：`LeaderElector` 缩为 control 启动锁；term/token 逐操作校验（publisher / manager / dispatcher / command consumer 四处）替换为 per-run `claim_epoch` 校验。

## Impact

- **数据库**：`t_agent_run` 新增 `claim_epoch`（int，default 0）、`heartbeat_at`（bigint，nullable）+ Alembic migration（纯加列，向前兼容；`owner_term` 保留为审计字段，退役评估列入后续）。
- **后端**：`run_dispatcher.py`（去 leader 化）、`agent_run_repository.py`（claim 升级 epoch + 心跳写）、`run_command_service.py`（消费分片）、`manager.py`/`publisher.py`（epoch 校验替换 term）、`server/bootstrap/`（三入口装配拆分）、`run_recovery_service.py`（阶段化重置）。
- **部署**：compose 三 service；`NOESIS_RUN_BUS_BACKEND=redis` 成为 worker>1 时的硬要求（memory 模式仅单 worker 部署合法，启动校验 fail-fast）。
- **SSE 内容流不变**：web 进程订阅远端 run 走既有 hub 路径；协议（序号/快照/终态 CAS）零改动。信令流发布向已走 bus（signal_bridge），**消费向跨 web 进程行为列为验收验证点**（不以断言替代验证）。
- 已知行为变化：owner 切换窗口（lease_ttl + 对账周期）内该 run 的命令延迟增加，`submit_and_wait` 更易走 accepted（语义正确，不伪装完成）。
- 删除：`app.py` 直启形态、LeaderElector 的 redis 选主分支、晋升回调里的 leader 面装配（control/worker 各自装配）。
