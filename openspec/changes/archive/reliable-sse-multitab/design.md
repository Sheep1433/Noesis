## Context

Noesis 当前已把 Run 创建与 SSE subscription 分开，并具备稳定 `run_id`、`assistant_message_id`、PostgreSQL checkpoint、Run 内 sequence、bounded replay buffer、每 subscriber bounded queue 和前端 gap recovery。这些是可以继续使用的基础。

当前还有六个已确认问题：

1. `RunService.publish_projected_event()` 先在 RunHandle lock 外修改 `RunProjection`，再进入 `RunManager.publish_attempt()` 分配 sequence。并发 subscribe 可能看到包含事件 N 的 projection，但 `snapshot_sequence` 仍是 N-1。
2. PersistSink 异步读取可变 projection，可能以 sequence N 写入已包含 N+1 内容的 snapshot；终态写入还没有强制 `snapshot` 与 `last_sequence` 同步更新。
3. Web 事件经过 `LangGraphSseBridge → SSE string → parser → RunProjection → SseDelivery`，与 RunEventBus / RunManager fan-out 重复。
4. 新 Tab 主要依赖当前 Tab 的 `sessionStorage` 或从消息历史推测 active Run，而不是查询服务端事实。
5. `run_manager`、producer、subscriber、HITL 和部分 channel runtime 是进程内状态；多 Uvicorn worker 会造成 owner 分裂。每个 worker 还会在 lifespan 中执行 recovery，后启 worker 可能中断其它 worker 正在执行的 Run。
6. SSE subscriber 和 persistence 共用“queue 满后注销”语义。慢 SSE 连接可以独立断开，但 persistence 不能静默丢失最新 checkpoint 或 terminal。

完整证据、外部方案比较和竞态时序见 `/Users/zzq/Library/Mobile Documents/iCloud~md~obsidian/Documents/knowledge-base/Interview/highlights/SSE/design/reliable-sse-multitab-refactor.md`（实现稳定后归档回 `docs/research/sse/`）。OpenSpec 只保留本次实施决策和可验收行为。

本 change 的产品与测试对象是 `COMMON_QA`、`FAULT_OPERATION_QA`、`SUPER_AGENT_QA` 和它们共用的 Web/Channel/HITL Delivery。`TEST_CASE_QA` 仍是独立 CaseCoordinator workflow；本 change 不迁移它的 `phase-*`、resume 或 export 路径，也不为它增加新验收用例。

## Goals / Non-Goals

**Goals:**

- 使 sequence、projection、status、replay buffer 和 snapshot revision 具有一个串行写入边界。
- 使业务 raw event 只经过一次 typed mapping，SSE 只在 Delivery 边界编码。
- 使任意 Tab 可从服务端发现、加入、停止或 resume 同一个授权 Run。
- 使一个 Tab 断开或慢消费只影响该 subscription，不影响 producer、persistence 和其它 Delivery。
- 使中间 checkpoint 可合并，但最新 checkpoint 不静默丢失，terminal 只在 PostgreSQL 事务成功后可见。
- 使“单 active backend”成为可检测的启动互斥，而不是口头约定。
- 同一次发布完成前后端、数据库和调用方切换，删除旧事件主路径。

**Non-Goals:**

- 不保证 SSE TCP 连接不断；可靠性来自 snapshot + sequence + reconnect。
- 不持久化每个 token/reasoning/tool-output chunk，不建立全量 event sourcing。
- 不引入 Redis Pub/Sub、Redis Streams、NATS、owner lease、heartbeat、fencing token 或跨进程 command routing。
- 不物理拆分 Web shell 与 Run worker，不用 WebSocket 替换浏览器 SSE。
- 不使用 BroadcastChannel leader Tab 转发 token。
- 不保留旧 EventBus、旧 SSE parser、v1/v2 双轨、双写、双读、feature flag 或旧客户端 adapter。
- 不迁移或扩展 `TEST_CASE_QA`、CaseCoordinator、`phase-*`、test-case resume 与 export。

## Decisions

### 1. 直接扩展 RunHandle 为单写入边界

`backend/packages/noesis-core/src/noesis/domain/chat/runs/manager.py` 继续管理 Run registry、producer 和 subscription。不新建 Actor framework，也不为每个 Run 新建 mailbox task。

`RunHandle` 保留一个 `asyncio.Lock`，并提供少量显式 API：

```text
apply_event(event, producer_generation, attempt_id)
subscribe(after_sequence)
advance_attempt()
begin_producer_segment()
finalize(terminal_intent)
stop()
```

`apply_event()` 在一个无 I/O await 的临界区中：

1. 检查 Run 未终态；
2. 检查进程内 `producer_generation` 和当前 model `attempt_id`；
3. 以 `last_sequence + 1` 分配 sequence；
4. reduce projection 并更新 status / last_sequence；
5. 构建 immutable `SequencedRunEvent`，写入 bounded replay buffer 和 subscriber queue；
6. 只在节流或语义边界命中时，在同一 lock 中复制 `snapshot@sequence`。

`subscribe()` 在同一 lock 内先注册 queue，再复制 snapshot 和可连续 replay，保证 snapshot 与 live tail 之间不丢事件。

**Alternatives considered:**

- 真 Actor + mailbox：对当前单 event loop 增加了 task lifecycle 和 command protocol，却不增加正确性，不采用。
- 在 projection 外层增加第二把 lock：会留下 lock ordering 和两个状态 owner，不采用。

### 2. RuntimeEventMapper 是唯一 raw event 映射入口

`backend/packages/noesis-core/src/noesis/domain/chat/streaming/` 中的 mapper 直接消费 LangGraph/LangChain raw event dict，输出封闭 typed `RunEvent` union。它是无状态模块，不做 plugin registry 或深类层级。

目标数据流：

```text
Runtime raw event
  → RuntimeEventMapper
  → RunHandle.apply_event
  → immutable envelope / snapshot
  ├─ SseDelivery
  ├─ PersistWriter
  └─ ChannelDelivery
```

SSE formatting 只存在于 `domain/chat/delivery/sse.py`。Web、Telegram、飞书、cron 和 eval 中属于本 change 范围的 Agent Run 共用 typed path。`LangGraphSseBridge → SSE string → parse_sse_line_to_event`、Web RunEventBus 和重复 RunLifecycle registry 在调用方迁移后删除。

`TEST_CASE_QA` 的 CaseCoordinator 不是该 mapper 的迁移输入；任务和契约测试不包含 `phase-start/delta/end`。

**Alternative considered:** 保留 SSE 字符串作为内部中间表示可以降低短期改动，但会继续要求 parser/encoder 双向同步，不符合一次切换约束，不采用。

### 3. PersistWriter 使用 latest-wins checkpoint 和 terminal barrier

Persistence 不再是可被 queue overflow 注销的普通 subscriber。每个 Run 最多保留一个待写中间 checkpoint：只允许更大 `snapshot_sequence` 覆盖更小值。tool 终态、HITL pending 等 semantic checkpoint 立即唤醒 writer；terminal 清除更旧 pending checkpoint 并拥有最高优先级。

`CheckpointRequest` 携带 immutable snapshot 及它的 sequence，PersistWriter 不得稍后重新读取可变 projection。普通 checkpoint transaction 必须同时更新：

```text
t_agent_run.snapshot
t_agent_run.last_sequence
t_agent_run.attempt_id / status / retry metadata
t_chat_message.content
```

并以 incoming sequence 不小于 stored sequence 为条件，防止迟到 checkpoint 覆盖新状态。

Terminal 使用 `finalize()` 和 compare-and-set：

1. lock 内设置 `finalizing`，预留 terminal sequence N；
2. clone `projection@N-1`，只在 clone 上 apply terminal，得到 candidate snapshot/envelope@N；
3. lock 外提交 run + assistant terminal transaction；
4. `Committed` 后才把 live projection 切换为 N 并 fan-out terminal；
5. `AlreadyFinalized` 时采用数据库终态 snapshot，不发布冲突 terminal；
6. 持久化 budget 内仍失败时，停止 producer，不发布伪终态，仅保留一个 immutable terminal candidate 低频重试。

**Alternative considered:** 终态先 fan-out 再异步落库更快，但会让客户端看到数据库中不存在的 completed，不采用。

### 4. 多 Tab 通过服务端 active Run 发现

新增：

```http
GET /api/chat/sessions/{session_id}/active-run
```

该端点经 `RunService` 和 repository 按 `(session_id, current_user_id)` 查询，返回与 `GET /api/chat/runs/{run_id}` 相同的完整 RunSnapshot 或 `data=null`。不存在、已删除或不属于当前用户的 session 返回 404。

前端进入 session 时并行加载 messages、attachments 和 active Run。active snapshot 以 replace 语义覆盖历史中同 `assistant_message_id` 的 streaming assistant，然后从 `snapshot_sequence` 订阅。`sessionStorage` 只能作为当前 Tab 的延迟优化，不得是恢复前提。

同 session 并发创建冲突时，`POST /api/chat/runs` 返回 409，`data` 包含当前用户所属的 `run_id`、`assistant_message_id`、`session_id` 和 status。前端加入该 Run，不创建第二 producer。

### 5. 前端只使用 snapshot replace + 连续 sequence apply

`frontend/src/views/chat/useSSEStream.ts` 保持每 session/run 的 `last_sequence` 与 subscription generation：

```text
sequence <= last_sequence      ignore
sequence == last_sequence + 1  apply
sequence > last_sequence + 1   stop reader, GET snapshot, replace, resubscribe
```

无终态 EOF、网络错误、subscriber overflow 和页面重新 visible 都进入带抖动退避的 recovery，而不触发成功/失败终态回调。旧 subscription generation 返回的迟到事件直接丢弃。

每个 Tab 直接订阅服务端，不使用 BroadcastChannel leader election。每 Run、每用户和全局 subscription 均有可配置上限；开流前超额返回 429/`SSE_SUBSCRIPTION_LIMIT`。

### 6. stop 和 HITL resume 使用同一 Run 命令边界

stop 和 HITL resume 都按 `(run_id, current_user_id)` 鉴权。首次 stop 在 RunHandle lock 内设置 `cancel_requested` 并只 cancel producer 一次；重复 stop 等待同一 terminal completion，不创建第二 terminal request。

每次初始 producer 或 HITL resume producer 启动前，RunHandle 递增仅进程内可见的 `producer_generation`；publisher callback 捕获该值，旧 task 迟到事件不分配 sequence。HITL resume 本身不等于 model retry；只有真正开始新模型 attempt 时才递增 `attempt_id`。

### 7. 使用 PostgreSQL advisory lock 强制单 active backend

`backend/server/main.py` lifespan 在 migration、`RunRecoveryService.recover_orphaned_runs()`、scheduler 和 channel runtime 启动之前，通过专用 PostgreSQL 连接请求固定 application advisory lock。连接在整个 lifespan 中保持，进程退出或数据库连接断开时自动释放。

无法获取 lock 的 worker 必须在接收流量前启动失败。不只检查 `WEB_CONCURRENCY` 或 Uvicorn argv，因为它们不能覆盖多容器和其它进程管理器。

如果数据库仍有非终态 Run，但本进程找不到 RunHandle，stream 端点在建立 `StreamingResponse` 前返回 503/`RUN_OWNER_UNAVAILABLE` 并告警，不创建第二 producer。

**Alternatives considered:**

- Redis Pub/Sub：不提供历史与 offset，也不修复 snapshot 竞态，不采用。
- sticky session：无法处理新 Tab、worker 退出、stop/HITL 命令路由与启动 recovery，不采用。
- 仅检查 `--workers`：无法防止第二个容器，只作辅助诊断。

### 8. 一次切换，不保留历史兼容路径

开发可以按依赖顺序分步提交，但中间状态不部署给用户。每个调用方接入新 typed path 后立即删除旧路径，最终发布不包含 old/new switch、双写、双读、schema adapter 或 v1 golden fixture。

此约束只排除历史实现兼容代码，不允许丢失已终态的聊天消息。数据迁移必须保留现有 terminal assistant content；发布前 active Run 不续跑，drain 后仍未完成者收口为 `interrupted`。

## Risks / Trade-offs

- **[Risk] 单 worker 可能成为容量上限** → 以 100 active Run、每 Run 2–3 Tab 为基线压测 event-loop lag、event-to-client latency、RSS、checkpoint lag 和 overflow 隔离；先移出 event loop 中的同步工作，只在指标证明必要时另立多实例 change。
- **[Risk] subscriber/replay 上限造成高内存** → 同时限制 event 数和 bytes，对慢 subscriber 独立断开，压测后仅通过配置调整上限。
- **[Risk] terminal DB 失败使 UI 长时间处于未完成** → 停止 producer、不伪造终态、保留一个 immutable candidate 低频重试，并暴露 persistence blocked 指标与用户可重试状态。
- **[Risk] advisory lock 专用连接断开** → 将 lock connection 生命周期纳入 lifespan；连接丢失时实例立即退出或变为 not-ready，不继续拥有 live Run。
- **[Risk] 前后端一次切换无混部缓冲** → 发布前 drain/收口 active Run，备份数据库，在隔离环境执行完整 migration + 双 Tab smoke test 后再切流量。
- **[Trade-off] 不迁移 TEST_CASE_QA** → 本 change 的可靠性收益不覆盖 CaseCoordinator、`phase-*` 和 test-case resume；它们不能作为本 change 不通过的验收项，也不能迫使新主路径保留兼容 parser。

## Migration Plan

1. 建立 N/N-1 竞态、迟到 checkpoint、stale producer/attempt 和 terminal barrier 的确定性失败测试。
2. 把 projection mutation 移入 RunHandle lock，实现 immutable snapshot 与 PersistWriter，同步修正 repository checkpoint/terminal transaction。
3. 将 Web Agent Run 迁移到 RuntimeEventMapper 和 SseDelivery；再迁移 Telegram、飞书、cron 和 eval 中属于目标 qa_type 的调用方。删除旧 RunEventBus、内部 SSE parser 和重复 lifecycle registry。
4. 新增 active-run API、409 join 语义、前端 snapshot state machine、subscription 配额和双 Tab E2E。
5. 在 lifespan 增加 advisory lock，完成第二 worker fail-fast、owner 状态丢失 503、recovery 与容量压测。
6. 发布前停止新 Run，drain 现有 Run，剩余收口为 interrupted；备份数据库，同时部署 migration、backend 和 frontend。
7. 发布后执行双 Tab、stop、HITL、断线恢复、terminal durability 和单 worker lock smoke test，再开放新 Run。

本 change 不在应用中保留旧路径作为回滚手段。如 migration 或 smoke test 失败，停止服务，用发布前数据库备份和对应代码整体恢复；失败发布窗口不接收新 Run。

## Open Questions

- 100 active Run、每 Run 2–3 Tab 时，snapshot deepcopy、SSE JSON 编码和 replay/subscriber bytes 的实测成本是多少？
- 单 Run/单用户/全局 subscription 的初始默认值是否需要在压测后调整？
- PostgreSQL 故障注入下，中间 checkpoint 合并率、terminal 抢占延迟和低频重试对连接池的影响是多少？
