# 决策：进程角色三分 web / control / worker（worker-role-split）

状态：implemented
日期：2026-09-23

## 问题

单活跃 backend 形态下全部后台执行面挤在一个 leader 进程：容量上限 = 单进程（验收 100 并发 run）、执行面无法水平扩展、leadership 选举 + term 校验管道 + 换主对账的全部复杂度都为"单持有者"这一个事实服务。审计观察 B（`docs/bug/2026-09-21-server-layer-layering-audit.md`）定稿目标形态：部署声明唯一性替代运行时选举。

现状基础（enable-distributed-sse-pubsub 已落地）：`t_agent_run` 已有 owner 字段、`claim_queued` 已是 CAS、`launch_payload` 已 schema 化、bus 唤醒与命令租约已存在——本变更是收口而非新建。

## 决策

### 1. claim_epoch fencing：单调递增、永不归零，双层设防

`t_agent_run` 新增 `claim_epoch`（每次认领 `+1`）与 `heartbeat_at`。epoch 校验两层：内存层（`apply_event` 拒绝路径）+ DB 层（checkpoint / 终态 CAS 的 UPDATE 带 `WHERE claim_epoch = :epoch`，僵尸写 rowcount=0 被拒）。失去持有的检测由每 run 心跳协程承担（lease 60s / 间隔 20s，落空即停本地执行，DB 抖动不误判）。

**epoch 归零是 fencing bug**（spec 自审发现并修正）：归零会让第一代超长假死僵尸的旧 epoch 与重置后再认领的新值碰撞、通过校验造成双写——重置只清 owner/heartbeat，epoch 保留。

### 2. 阶段化重置：未碰世界才可重排队

对账分流判据：`last_sequence=0 且无 snapshot 且 launch_payload 可重建` → 重置 queued 等待再认领；已执行 → 收口 interrupted（副作用不可重放原则不破）。判据安全性：**工具执行前必先发布 tool-input 事件**，故 last_sequence=0 的 run 工具不可能执行过（模型调用幂等可重跑）。

### 3. 认领：CAS（已有）+ SKIP LOCKED 圈行（新增，纯优化）

`claim_next_batch`：`FOR UPDATE SKIP LOCKED` 圈行 + 锁内逐行容量回调判定 + CAS。正确性由行锁 + CAS 双保险，SKIP LOCKED 只省多 worker 同批竞争的空转。容量检查经回调注入（锁内判定，满的行不认领保持 queued）。

### 4. 三入口、无过渡态

`app.py`（单进程全干）删除；`web.py` / `control.py` / `worker.py` 三入口同镜像不同 command。`NOESIS_RUN_BUS_BACKEND=redis` 成为硬要求（三进程下事件/唤醒跨进程），memory 模式启动 fail-fast。对账按"谁持有状态谁对账"拆两组：control 对账（主 run 阶段化 + 定时任务）、worker 对账（executor 热集 + 命令重置 + 通知装载），顺序清单有测试钉住。

### 5. owner_term 语义迁移为 claim epoch

`RunEventEnvelope.owner_term` 的值从 leader term 改为 claim epoch——hub 侧迟到过滤机制（旧值丢弃、新值抬高权威）对 epoch 语义天然兼容，hub 零改动。publisher 的全局 token 逐条校验删除（失去持有由心跳停 run、事件源头枯竭；DB 层拒绝迟到持久化）。

### 6. 命令消费分片

`claim_pending` 支持 `shard_filter`：圈行后 Python 侧过滤，只认领"命令目标在本进程持有"的命令（run_manager 注册表 / executor 热集），未持有的留锁释放给 owner worker。认领租约（claimed 超时重置）原样保留。

## 备选方案

- **保留 auto/web/worker 三态（auto=单进程全干兼容）**：被否——多套装配路径并行都要测，违反"禁止多套方案并行"；观察 B 定稿明确无过渡态。
- **epoch 归零简化重置**：被否——碰撞窗口让 fencing 失效（见决策 1）。
- **claim 带 heartbeat 超时条件（心跳超时可直接抢）**：被否——绕过对账的阶段化检查（未碰世界判据），心跳超时判定只属于对账。
- **心跳复用 updated_at**：被否——通用审计戳被任何写碰，心跳被非存活信号污染（仓库既有事故：会话列表排序被后台写污染）。
- **僵尸一律重置 queued**：被否——已执行工具的 run 重跑等于重复执行外部操作。
- **Kafka/Celery 外包执行面**：被否——run 长生命周期、有副作用、不可重跑；队列只解决分发不解决"活任务唯一主人"（问题整理 Q23 论证）。
- **worker 间 gossip 探活**：被否——数据库行（heartbeat + epoch）已是充分共享事实源，进程间协议层是负复杂度。
- **`pg_manager.try_acquire_advisory_lock` 命名**：实现时核实为 `try_advisory_lock` / `acquire_advisory_lock`（elector 同款 API），control 复用 acquire（fail-fast 语义）。

## 验证

三 Phase 分步验收：Phase 1（fencing 数据层 + 对账阶段化）与 Phase 2（worker 化认领 + 命令分片）单测全绿（epoch 单调性/僵尸写双层拒绝/心跳三退出路径/双 worker 竞争语义）；Phase 3 装配契约测试（三 app 构建、memory fail-fast、双对账组顺序）+ 契约门禁 25/25（TestClient 经 `server.main` 兼容层指向 web app，CSRF 挂载守卫原样通过）。全量单测 1660 绿。

待环境项（tasks 4.3/4.4/6.2/6.3 对应）：容量脚本前后对比、Redis 双进程/三进程集成（双 worker 并发认领无双跑、kill -9 后 lease_ttl 收口）、三进程手动验收（信令消费向跨 web、双标签页 SSE 一致性）。

## 追加决策：all-in-one 单进程入口保留（2026-09-23，产品决策）

三入口落地后用户反馈：本地简单自用场景需要零额外依赖的单进程形态——
「多套方案并行」的禁令针对的是**同一问题域里的死方案**，而 all-in-one
（memory 总线）与三入口（redis 总线）是**两种部署场景**，共享全部业务
装配件，属于 RunBus 适配器模式的正常形态。

- 新增 `build_all_in_one_app`（entries.py）：单进程 web 路由 + control
  singleton + worker 执行面一体，memory/redis 总线均可（memory 为本地
  默认）；advisory lock 保留（防手滑双开——memory 总线无跨进程感知，
  双写不可检测，只能靠锁预防）。
- `app.py` 恢复为 all-in-one 入口；web/control/worker 三入口保持
  redis fail-fast 不变。
- dev.sh 按 `NOESIS_RUN_BUS_BACKEND` 分支启动（memory → app.py 单进程；
  redis → 三进程），dev 栈自动拉起 Redis 容器（start_redis）。
- 验收追加：app.py + memory 端到端通过（run completed、SSE 7 事件
  严格递增、run.finished + [DONE]）。

## 影响

执行面水平扩展路径打通：`--scale worker=N` 容量线性、`--scale web=N` HTTP 面扩展；全局 leader 选举与 term 管道退役（LeaderElector 仅剩 control 启动锁用途，redis 选主分支待清理）；dev/prod 启动从单进程变三进程（run.sh / compose / 文档已同步）。已知行为变化：owner 切换窗口（lease_ttl + 对账周期）内命令延迟增加，`submit_and_wait` 更易走 accepted（语义正确不伪装完成）。
