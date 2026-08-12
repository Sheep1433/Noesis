# Durable Agent Run 与断线恢复架构

> 状态：Current
> 关联 OpenSpec：`agent-delivery`、`platform-chat`、`container-deployment`

## 1. 目标与边界

Durable Agent Run 把逻辑执行、持久化与 Delivery 从浏览器连接中分离。Tab 刷新、关闭、断网或某个 Channel 失败时，producer 继续；客户端通过 PostgreSQL snapshot 与进程内 replay 恢复。

当前 durable 指“消息和 Run 状态可恢复”，不表示 Python coroutine 可跨进程恢复。进程重启后不重放可能产生副作用的模型或工具调用。

## 2. RunHandle 单写入模型

每个 live Run 只有一个 `RunHandle`。以下状态只能在它的 `asyncio.Lock` 内修改：

- projection 与 Run status；
- `next_sequence`、attempt 与 producer generation；
- replay buffer；
- SSE/Channel subscriber 注册；
- pending terminal candidate 与 cancel intent。

`apply_event()` 在无 I/O await 的临界区中检查 generation/attempt、reduce projection、分配 sequence、构建 immutable envelope，并对非终态事件 fan-out。`subscribe()` 在同一把锁内先注册 queue，再复制一致的 snapshot/replay，因此不存在 projection 已包含 N、snapshot sequence 仍为 N-1 的窗口。

每次初始执行或 HITL resume 前递增 `producer_generation`。旧 task 的迟到事件在 projection reduce 前被拒绝。真正的新模型尝试使用独立 `attempt_id`；两者语义不能混用。

## 3. PersistWriter

Persistence 不是普通 subscriber。每个 Run 的 PersistWriter 只有：

- 一份正在写的 immutable `CheckpointRequest`；
- 一个 pending 单槽；
- 一个 wakeup；
- 一个 writer task。

当 N 正在写入，N+1…N+k 到来时，pending 只保留 sequence 最大的请求。semantic checkpoint 立即唤醒；普通正文按时间节流。请求携带捕获于 RunHandle lock 内的 `snapshot@sequence`，writer 不重新读取 live projection。

Repository 使用 `stored last_sequence <= incoming sequence` 作为 guard。只有 Run checkpoint 更新成功，才在同一事务中更新 assistant content；迟到 checkpoint 不触碰 assistant。

## 4. Terminal persistence barrier

terminal intent 不直接修改 live projection：

```text
projection@N-1
  → clone
  → clone.apply(terminal)
  → immutable candidate@N
  → PostgreSQL CAS transaction(run + assistant + snapshot + last_sequence=N)
       ├─ committed         → swap live projection，buffer/fan-out terminal，完成 Run
       ├─ already_finalized → 采用 DB snapshot，fan-out snapshot replace
       └─ failed            → 不发布 terminal，保留一个 candidate 低频重试
```

因此 terminal、`[DONE]` 和 terminal snapshot 只会在数据库事务成功后到达客户端。checkpoint writer 会丢弃不大于 terminal sequence 的 pending 请求。

## 5. API 与多 Tab

主要接口：

- `POST /api/chat/runs`：幂等创建；同 session active 冲突返回 409 join data。
- `GET /api/chat/runs/{run_id}`：读取 live 或 DB snapshot。
- `GET /api/chat/sessions/{session_id}/active-run`：新 Tab 的权威发现入口。
- `GET /api/chat/runs/{run_id}/stream`：独立 bounded SSE subscription。
- `POST /api/chat/runs/{run_id}/stop`：按 Run 鉴权且幂等。
- `POST /api/chat/runs/{run_id}/hitl/resume`：同一身份续跑，CAS 防重。

每个 Tab 独立订阅。Run、用户和全局均有 subscription 配额。关闭一个 Tab 不会触发 stop，也不会删除其它 Delivery。

## 6. 状态与恢复

```text
queued → running ↔ retrying
             ↕
        hitl_pending
             ↓
completed | partial | error | interrupted
```

启动 recovery 只在 advisory lock 获取成功后执行。它把数据库中无 live owner 的非终态 Run 收口为 `interrupted/server_restart`，保留最近 snapshot，将 assistant 收口为 partial，不调用模型和工具。

终态 Run 在配置的 retention 后释放 producer task、projection、buffer、subscriber 与 persistence writer；后续查询从 PostgreSQL 返回。

## 7. 并发、权限与容量

- 同一 session 最多一个 active Run；数据库约束与创建事务负责最终互斥。
- create/get/stream/stop/HITL 均按 `(run_id, current_user_id)` 鉴权。
- active-run 先验证 session 所属用户，跨用户统一 404。
- active Run、单用户 Run、输出 bytes、run duration、HITL timeout、replay bytes、subscriber queue 与 terminal retention 均有明确上限。
- PostgreSQL advisory lock 保护单 active backend；不能通过增加 Uvicorn worker 扩容。

## 8. 失败处理

- 短暂 checkpoint failure：PersistWriter 保留最新 pending 并重试。
- 持续 persistence failure：停止 producer，不伪造 completed/error，不无界积压。
- 慢 SSE：只移除该 subscriber，由 snapshot 恢复。
- Channel failure：记录独立 delivery failure，不污染 Run 终态。
- terminal 竞争：CAS loser采用 DB 权威状态。
- owner 不可达：非终态 stream 返回 503，不创建第二 producer。
- lock connection 丢失：health 变为 not-ready、拒绝新 Run并取消 live producer。

## 9. 可观测性

RunManager 暴露：

- active/retained Run；
- buffer/subscriber queue events 与 bytes；
- subscriber overflow、重连与拒绝次数；
- event-loop lag、event-to-client latency；
- checkpoint latency、lag、失败与 coalescing；
- persistence blocked、terminal CAS loser；
- cancel latency 与 terminal reclaimed。

日志以 `run_id` 关联 terminal candidate、checkpoint failure、stale generation/attempt、subscription overflow 与 owner lock loss。

## 10. 代码入口

- `backend/packages/noesis-core/src/noesis/domain/chat/runs/manager.py`
- `backend/packages/noesis-core/src/noesis/services/run_service.py`
- `backend/packages/noesis-core/src/noesis/services/channel_run_service.py`
- `backend/packages/noesis-core/src/noesis/repositories/agent_run_repository.py`
- `backend/packages/noesis-core/src/noesis/services/run_recovery_service.py`
- `backend/packages/noesis-core/src/noesis/storage/postgres/manager.py`
- `frontend/src/views/chat/useSSEStream.ts`

## 11. 验证方式

确定性测试覆盖 apply/subscribe 竞态、immutable checkpoint、latest-wins、terminal barrier、CAS loser、stale generation/attempt、stop/HITL 幂等、429/503、advisory lock loss 与 Channel isolation。

容量脚本 `backend/tests/load_test.py` 使用 100 active Run、每 Run 2–3 Tab、每 Run 10–30 events/s，并混入慢消费和重连，输出 p50/p95/p99、event-loop lag、RSS、queue bytes、checkpoint lag、overflow 与回收数量。

## 12. 已知限制

- 只支持单 active backend，不支持 owner lease、fencing 或跨进程接管。
- 进程崩溃只保留最近 checkpoint，内存 replay token 会丢失。
- Channel outbound 不是 durable spool。
- PostgreSQL 持续不可用时，系统选择有界停止而不是继续生成无法保存的内容。
- `TEST_CASE_QA` 不使用本 Run 主路径。

## 13. 关联资料

- [SSE 流式架构](chat-streaming.md)
- [发布 Runbook](../../engineering/reliable-sse-release-runbook.md)
- `openspec/changes/reliable-sse-multitab/`
