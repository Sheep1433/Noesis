# Design: 进程角色三分（web / control / worker）

## 1. 目标形态

```
浏览器 ──▶ LB ──▶ web × N（无状态：HTTP、认证、SSE 转发、hub 订阅、快照查库）
                control × 1（advisory lock 防双开：定时调度、Telegram/飞书通道、
                            记忆抽取与整理、孤儿对账、排队任务入队）
                worker × N（认领 queued run 并执行；持有期间消费该 run 的命令；
                            心跳续期；沙箱生命周期归 worker）
存档层：t_agent_run（owner/claim_epoch/heartbeat）+ 命令表 + bus_redis —— 唯一事实源
```

## 2. 认领与 fencing（核心原语）

### 2.1 claim 升级：epoch 递增 + 心跳

现有 `claim_queued` 的 CAS 条件（`status=queued AND owner IS NULL AND owner_term=0`）在"对账重置后二次认领"场景失效——重置后 term 归 0 可以再次 CAS，但**第一个认领者若仍活着**，它内存里的执行流与第二个认领者并发写同一 run。修复：

```sql
-- 认领（条件更新，天然多消费者安全；只认领对账清场后的行）：
UPDATE t_agent_run
SET owner_instance_id = :worker_id,
    claim_epoch = claim_epoch + 1,     -- 每次认领递增，永不归零
    heartbeat_at = :now
WHERE id = :run_id AND status = 'queued' AND owner_instance_id IS NULL
-- 高并发认领竞争（多 worker 同批 queued）时升级为：
SELECT ... FOR UPDATE SKIP LOCKED LIMIT batch  先圈行再逐行 CAS（纯优化，正确性不依赖）
```

两条层间纪律：

- **heartbeat 超时判定只属于对账**（§2.2 的分流入口），不属于 claim——心跳超时的 running run 必须先经对账阶段化分流（重置时清 owner / 标记为 interrupted），claim 永远只见 `queued AND owner IS NULL` 的行。把超时判定放进 claim 等于绕过"是否已碰世界"的检查。
- **epoch 单调递增、永不归零**：对账重置只清 `owner_instance_id` 与 `heartbeat_at`，`claim_epoch` 保留。归零会制造 epoch 碰撞——第一代认领 epoch=1 的超长假死僵尸，在"归零→再认领到 1"后苏醒，epoch 相等即通过校验，双写。

- **epoch 校验管道**：事件信封携带 `claim_epoch`（发布时从 handle 读）；`apply_event` 与 publisher 提交前比对 DB 侧 epoch——不符即拒（`StaleProducerGeneration` 的同款防御，对象从"全局 leader term"细化为"单 run 认领代次"）。写入侧的终极防线：checkpoint / 终态 CAS 的 UPDATE 带上 `WHERE claim_epoch = :epoch`，僵尸 worker 的迟到写在数据库层被拒。
- **心跳**：worker 持有期间周期写 `heartbeat_at`（间隔 = lease_ttl / 3）。不复用 `updated_at`——通用审计戳会被任何写碰（仓库既有教训：业务语义时间字段必须独立）。
- **`owner_term` 保留为审计字段**（谁在哪个历史阶段 claim 的），不再参与 fencing 判定；退役评估列入后续。

### 2.2 阶段化重置（对账的正确性边界）

僵尸判定（control 周期扫描 + 启动对账共用）：

```
running/hitl_pending/retrying 且 heartbeat_at 超过 lease_ttl
  ├─ last_sequence = 0 且 snapshot IS NULL 且 launch_payload 可用
  │    → 「未碰世界」：重置 queued + 清 owner_instance_id 与 heartbeat_at
  │      （claim_epoch 保留递增，见 §2.1 层间纪律）
  └─ 已有持久事件或 checkpoint
       → 「已碰世界」：标记为 interrupted（服务重启语义），
         部分成果按落库投影保留——副作用不可重放原则不破
```

判据字段全部现成（`last_sequence` / `snapshot` / `launch_payload`），无需新增。

已知行为变化（接受并标注）：owner 切换窗口 = lease_ttl 超时判定 + 对账扫描周期 + 再认领，窗口内该 run 的命令（stop / HITL resume）延迟增加，`submit_and_wait` 有界等待更容易走 accepted 分支——语义正确（accepted 不伪装完成，前端继续订阅等终态），延迟代价随 lease_ttl 配置声明。

### 2.3 命令消费分片

命令表新增消费过滤：worker 扫描命令时 JOIN run 归属——`run_id` 属于本进程持有（内存 handle 注册表命中）才认领执行；不属于本进程的命令留给其 owner worker 的扫描周期。全局命令（无 run 归属的少数类型，如有）由 control 消费。认领租约机制原样保留（防 worker 崩溃时命令卡 claimed）。

## 3. 三入口装配

`app.py` 退役为装配库（`server/bootstrap/` 下已有 `leader_runtime.py` 的模块化基础），三入口各自组装：

| 入口 | lifespan 装配 | 不装配 |
|---|---|---|
| `web.py` | DB、KB（查询面）、路由、CSRF、request_id、信令桥（发布向已走 bus，task 4.7） | dispatcher、命令消费、调度器、通道、记忆任务、沙箱、checkpointer（执行恢复状态，纯 worker 面） |
| `control.py` | DB、调度器（定时触发创建 run 写库 queued）、通道、记忆任务、孤儿扫描对账（advisory lock 防双开） | HTTP 路由（仅 /health）、run 认领与执行、沙箱 |
| `worker.py` | DB、checkpointer、KB、认领循环、命令消费（分片）、沙箱与 sandbox-runner 自动拉起、executor/隔离循环 | 调度器、通道、记忆任务、HTTP 路由（仅 /health） |

queued run 的认领**只归 worker**；control 与 run 的唯一交集是定时任务到点创建 run（调度器职责的自然部分），不参与排队任务的分发。

- `NOESIS_RUN_BUS_BACKEND=redis`：worker > 1 时启动 fail-fast（memory 模式仅允许单 worker——bus 无法跨进程广播事件）。
- `/health` 角色字段如实上报（`role: web|control|worker`），执行锁语义变为 control 锁 + per-run 租约。

## 4. 被否方案

| 方案 | 否决理由 |
|---|---|
| 保留 auto/web/worker 三态（auto=单进程全干） | 多套方案并行：两套装配路径都要测，收敛遥遥无期；观察 B 定稿明确无过渡态 |
| Kafka/Celery 外包执行面 | 任务长生命周期、有副作用、不可重跑；队列只解决分发不解决"活任务唯一主人"（已论证，见问题整理 Q23） |
| 全局 leader term 继续当 fencing 用 | term 是全局单调，无法表达"单 run 的认领代次"——对账重置后二次认领同 term，僵尸判定失效 |
| 心跳复用 updated_at | 通用审计戳被 checkpoint/命令等任何写刷新，心跳会被非存活信号污染（复用通用时间戳的既有事故：会话列表排序被后台写污染） |
| 僵尸一律重置 queued | 违反副作用不可重放——已执行过工具的 run 被重跑等于重复执行外部操作 |
| worker 间 gossip 互相探测存活 | 引入进程间协议；数据库行（heartbeat + epoch）已是充分的共享事实源，加协议层是负复杂度 |

## 5. 风险与边界

- **migration**：纯加列（`claim_epoch` default 0、`heartbeat_at` nullable），存量行无需回填（0 = 未 fencing 语义前认领，首次重置/认领后进入新语义）。
- **双写窗口**：epoch 校验在投影（内存）与 checkpoint/终态（DB）双层设防，内存层拒绝是快速失败、DB 层是终极防线——即使内存校验有 bug，DB 条件更新保证僵尸写不落库。
- **控制面单点**：control × 1 故障窗口内定时任务延迟、通道消息堆积（failover：advisory lock 重竞选，同现有机制）——观察 B 已标注其最终解（认领原语泛化到调度任务），本变更不做。
- **SSE 恢复**：web 面服务任意 run 重连走既有 hub + DB 快照路径，worker 挂掉时重连拿到的是最近 checkpoint（2 秒窗口语义不变，事件表仍是独立后续项）。
- **升级路径**：compose 三个 service 同镜像滚动替换；旧单容器部署需迁移到三 service（breaking，随本变更的部署文档明确）。

## 6. 验证策略

- 单测：epoch CAS 幂等与递增；**epoch 单调性回归**（对账重置后 epoch 不归零、跨多轮重置递增，超长假死僵尸的旧 epoch 永远不等于当前值）；僵尸写入被双层拒绝（内存 epoch 比对 + DB 条件更新）；阶段化重置的两个分支；命令分片过滤（本进程 run 命令消费、非本进程跳过）；心跳超时判定。
- 集成（真库）：双 worker 并发认领同批 queued run 无双跑；worker kill -9 后 lease_ttl 内被对账标记为 interrupted；重置 queued 的 run 被另一 worker 认领并执行到终态。
- 契约：`tests/api_contract` + `test_doc_contract` 全绿；`test_leader_runtime_order.py` 对账顺序语义迁移到新对账模块后同步改写。
- 手动验收：三进程起本地栈，单 run 双标签页 SSE 一致性 + 停止命令链路 + 定时任务触发（control）全链路；**信令消费向跨 web 进程验证**（发布向已确认走 bus，signal_bridge task 4.7；双 web 进程下跨窗口实时刷新列为显式验证点——不以"SSE 面不变"的断言替代验证）。
