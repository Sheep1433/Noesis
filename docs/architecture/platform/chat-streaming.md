# SSE 流式数据架构

> 状态：Current
> 关联 OpenSpec：`platform-chat`、`agent-delivery`、`container-deployment`

## 1. 目标与边界

Noesis 的可靠性目标不是维持一条永不断开的 TCP 连接，而是让 Agent Run 独立于浏览器连接，并通过权威 snapshot、单调 sequence 和重新订阅恢复界面。

本架构适用于 `COMMON_QA`、`FAULT_OPERATION_QA`、`SUPER_AGENT_QA` 的 Web、Channel 和 HITL 路径。`TEST_CASE_QA` 仍由独立 CaseCoordinator 处理，不进入本管线。

## 2. 核心约束

1. 浏览器 subscription 不是 producer owner；关闭、刷新或慢消费只移除当前连接。
2. Runtime raw event 只映射一次，SSE 字符串只在 HTTP Delivery 边界产生。
3. sequence、projection、status、replay buffer 与 subscriber 注册由同一个 `RunHandle.lock` 串行化。
4. checkpoint 可 latest-wins 合并；terminal 必须先在 PostgreSQL 提交，再对客户端可见。
5. 同一 Run 的所有 Tab 使用相同 `run_id`、`assistant_message_id` 和权威终态。
6. 当前 live owner 在进程内，因此部署只允许一个 active backend。

## 3. 组件与职责

```text
LangGraph / LangChain raw event
  → RuntimeEventMapper
  → RunManager.apply_event
       ├─ immutable replay envelope → SseDelivery → Browser Tab A/B/...
       ├─ immutable checkpoint      → PersistWriter → PostgreSQL
       └─ immutable envelope        → ChannelDelivery

（子 Agent 同源：executor 消费 stream_agent_events + 同一 mapper，
 产出子会话投影与 run 事件，见 docs/architecture/subagent-sessions.md「统一 run 管道」）
```

- `RuntimeEventMapper`：将 raw event 转为封闭 typed `RunEvent`。不编码 SSE，不做持久化。主/子
  Agent run 共用（子 Agent 的 usage 累计、上下文快照、HITL 投影由同一条 bridge 状态机产出）。
- `RunHandle`：唯一 live state owner，维护 projection、sequence、状态、producer generation、replay 与订阅者。
- `PersistWriter`：每 Run 单槽 latest-wins writer，不属于 subscriber 集合，不受 SSE overflow 策略影响。
- `SseDelivery`：把 typed event 编码为 SSE；连接失败只释放当前 queue。
- `RunService`：权限、active Run、checkpoint/terminal transaction 与 RunManager 编排。
- `AgentRunRepository`：以 sequence guard 写 checkpoint，以 compare-and-set 抢占唯一 terminal。

## 4. 调用与数据流

### 4.1 创建和订阅

```text
POST /api/chat/runs
  → 单事务创建 user message + assistant skeleton + queued run
  → commit 后注册 RunHandle 并启动 producer
  ← run_id + assistant_message_id

GET /api/chat/runs/{run_id}/stream?after_sequence=N
  → 按 run/user 鉴权并检查配额
  → lock 内先注册 bounded queue
  → 返回 snapshot；buffer 连续时附带 sequence>N 的 replay
  → live events
  → 已提交 terminal + [DONE]
```

同 session 已有 active Run 时，创建返回 409 和可加入的 `run_id`、`assistant_message_id`、`session_id`、status，不启动第二个 producer。

### 4.2 新 Tab 与断线恢复

新 Tab 通过 `GET /api/chat/sessions/{session_id}/active-run` 查询服务端事实。`sessionStorage` 只保存当前 Tab hint，不能作为恢复前提。

### 4.2a 信令流（跨窗口 / 会话列表实时刷新）

run 内容流之外有两条**轻量信令流**，只推定位符（`run-started | run-hitl-pending | run-terminal`），不推内容：

- `GET /api/chat/sessions/{session_id}/events`（event: `session-signal`）——同一会话的其它窗口（跨浏览器、跨设备）实时发现活跃 run，收到后经 `active-run` / `runs/{run_id}` 取权威状态并加入订阅。建连先下发当前 active run 作为首帧，覆盖「窗口先连、run 后建」之外的所有时序；超订阅数 429/`SESSION_SIGNAL_LIMIT`。
- `GET /api/chat/events/stream`（event: `user-signal`）——该用户**任意**会话的 run 状态变化（携带 `session_id`/`status`），会话列表据此 patch 行级 run_status；建连先下发用户全部活跃 run。超订阅数 429/`USER_SIGNAL_LIMIT`。

信令由 `RunManager` 在状态迁移点发布（`start()`/`resume()` 直接置 RUNNING 处，及 `transition()` 到 HITL_PENDING / 终态处；`chat/runs/{session_signals,user_signals}.py` 进程内总线，有界队列慢订阅丢帧）。**信令是 hint**：丢失或断线靠 `active-run` 自愈，不参与权威状态；流不主动结束，随页面关闭断开，15s 注释 keepalive。

客户端收到 `run-snapshot` 后按 `assistant_message_id` replace parts，并采用以下 sequence 规则：

```text
sequence <= last_sequence      忽略
sequence == last_sequence + 1  apply
sequence > last_sequence + 1   停止 reader，查询 snapshot，replace 后重订阅
```

无终态 EOF、网络错误、页面恢复可见或 subscriber overflow 都进入 snapshot recovery。旧 subscription generation 的迟到响应直接丢弃。

### 4.2b run 内容流事件清单

run 内容流（`/api/chat/runs/{run_id}/events` 与创建 run 的响应流）的完整事件词表如下。**本清单由契约测试钉住**（`backend/tests/test_doc_contract.py` 从 `langgraph_bridge.py` 提取事件名与本节比对）：新增或改名事件而未更新本节，CI 红。

| 分组 | 事件 |
|---|---|
| 消息与生命周期 | `message-start`、`finish`、`abort`、`error`、`run-status` |
| reasoning | `reasoning-start`、`reasoning-delta`、`reasoning-end` |
| 正文 | `text-start`、`text-delta`、`text-end` |
| 工具 | `tool-input-start`、`tool-input-available`、`tool-output-available` |
| 检索与统计 | `retrieval-results-available`、`stats-update`、`context-update` |
| HITL | `hitl-required` |
| Phase（TEST_CASE 遗留） | `phase-start`、`phase-delta`、`phase-end`、`scenario-start`、`testpoints-confirm-required`、`scene-cases` |
| 传输层哨兵 | `data: [DONE]`（流传输收尾，不表示业务终态；`chat/delivery/sse.py`） |

历史兼容：`tool-call-start` 是 `tool-input-start` 的旧名，仅在 `runs/projection.py` 的重放路径中作为别名接受，新代码不得发射；`token-details`、`finish-step` 已不存在于当前事件流。

### 4.3 HITL 与停止

HITL 使用同一 `run_id` 和 `assistant_message_id`：`running → hitl_pending → running → terminal`。暂停只结束 LangGraph 执行分段，不发送整个 Run 的 `[DONE]`。任意 Tab 可提交 resume；CAS 保证只启动一个新 producer segment。

stop 在 RunHandle lock 内只设置一次 `cancel_requested` 并 cancel producer 一次。取消路径产生一个 terminal candidate；重复 stop 等待同一 terminal，不影响同 session 后续新 Run。

## 5. 状态、数据与权限

Run status：`queued | running | retrying | hitl_pending | completed | partial | error | interrupted`。终态互斥且不可覆盖。

同一轮 Run 对应一行 assistant。checkpoint transaction 同时更新：

- `t_agent_run.snapshot/last_sequence/status/attempt/retry metadata`；
- `t_chat_message.content`。

terminal transaction 同时更新 Run 终态、完整 snapshot、`last_sequence` 与 assistant 终态。客户端看到 terminal 时，该事务已经提交。

create/get/active-run/stream/stop/HITL resume 均按当前 Cookie Session 用户鉴权。未知、已删除或跨用户 session 的 active-run 查询统一返回 404。

## 6. 失败处理与可观测性

- SSE queue 按事件数和字节数限制；溢出只断开慢 subscription。
- checkpoint failure 由 PersistWriter 重试，pending 单槽只保留最大 sequence。
- terminal 在 budget 内失败时保持非终态可见性，停止 producer，只保留一个 immutable candidate 低频重试。
- terminal CAS loser 采用 PostgreSQL 权威 snapshot，不发布冲突终态。
- 非终态 DB Run 找不到本地 RunHandle 时，stream 在开流前返回 503/`RUN_OWNER_UNAVAILABLE`，不重建 producer。
- subscription 超额在开流前返回 429/`SSE_SUBSCRIPTION_LIMIT`。

RunManager 指标包含 active/retained Run、subscriber/event/replay bytes、overflow、重连、event-loop lag、event-to-client latency、checkpoint latency/lag/coalescing、persistence blocked、terminal CAS loser 与回收数量。

## 7. 部署约束

lifespan 在 migration、recovery、scheduler 和 channel runtime 之前，通过专用 PostgreSQL 连接获取固定 advisory lock。第二个 worker/容器 fail-fast。lock 连接丢失后实例变为 not-ready、拒绝新 Run，并停止 live producer。

SSE 注释 keepalive 不分配 sequence，也不触发 checkpoint。反向代理 read timeout 必须大于 keepalive 间隔并关闭响应缓冲。

公网入口（宿主机 nginx，`/etc/nginx/sites-enabled/noesis`，certbot 管理）在 443 上启用 HTTP/2（`listen 443 ssl http2;`）：单 Tab 并发挂用户级信令流、主对话 run 流、子任务目录流与子会话 run 流，HTTP/1.1 下受浏览器同源 6 连接硬限，多 Tab / 多抽屉即占满导致请求排队挂起；h2 多路复用后所有流共用单连接（每连接并发 stream 上限约 100+），该瓶颈消除。容器内 nginx（`deploy/frontend/nginx.conf`）与 uvicorn 上游链路保持 HTTP/1.1 不变。

## 8. 代码入口

- Run 单写入边界：`backend/packages/noesis-core/src/noesis/chat/runs/manager.py`
- raw event mapper：`backend/packages/noesis-core/src/noesis/chat/event_mapping/mapper.py`
- SSE Delivery：`backend/packages/noesis-core/src/noesis/chat/delivery/sse.py`
- Run Service：`backend/packages/noesis-core/src/noesis/services/run_service.py`
- Repository：`backend/packages/noesis-core/src/noesis/repositories/agent_run_repository.py`
- API：`backend/server/api/chat_api.py`
- 前端状态机：`frontend/src/views/chat/useSSEStream.ts`
- 双 Tab E2E：`frontend/e2e/multi-tab.spec.ts`

## 9. 验证方式

- 后端：`cd backend && uv run pytest tests/ -q`
- 前端：`cd frontend && pnpm test && pnpm lint && pnpm build`
- E2E 列表：`cd frontend && pnpm exec playwright test --list`
- 容量：`cd backend && uv run python tests/load_test.py`
- 真实 PostgreSQL 双实例：设置 `NOESIS_LIVE_POSTGRES_TEST=1` 后运行 `tests/test_advisory_lock.py`

## 10. 已知限制

- 进程崩溃后只用最近 checkpoint 收口为 `interrupted/server_restart`，不重放模型或工具。
- 当前不支持多 active backend、owner 转移或跨进程 command routing；未引入 Redis Pub/Sub。
- Channel outbound 是进程内有界队列，不是 durable spool。
- `TEST_CASE_QA` 仍保留自己的旧 SSE 边界，不属于本架构的验收范围。

## 11. 关联资料

- [Durable Agent Run](durable-agent-runs.md)
- [研究与方案评审](/Users/zzq/Library/Mobile%20Documents/iCloud~md~obsidian/Documents/knowledge-base/Interview/highlights/SSE/design/reliable-sse-multitab-refactor.md)（实现稳定后归档回 `docs/research/sse/`）
- [发布 Runbook](../../engineering/reliable-sse-release-runbook.md)
- `openspec/changes/reliable-sse-multitab/`
