# Durable Agent Run 与断线恢复设计

> 状态：Current（核心 Web run 与恢复链路已实现；未完成能力在“已知限制”中列明）
> 关联 OpenSpec：`durable-agent-run-recovery`（change，已归档并入）、`agent-delivery`、`platform-chat`
> 外部实现核对：OpenAI Codex `95637f7056835fea66bdd0044414af480fc0fd74`；OpenClaw `8ecb609990ff191bb9258f5685f90cbdde7e6c17`
> 核对日期：2026-07-27

## 1. 目标与边界

本设计让 Agent run 独立于浏览器连接。刷新、短暂断网、某个 SSE/通道 Delivery 失败时，run 继续执行；客户端重新进入后读取权威快照并订阅后续事件。

P0 只保证同一后端进程内继续执行。后端进程重启后不恢复原 coroutine，也不自动重放工具；系统保留最近检查点并把悬空 run 收口为 interrupted。

不替换 SSE，不引入分布式任务系统，不在本设计内解决多 backend 实例的 owner 转移。

因此 P0 生产部署必须使用单 active backend，或确保 run 的 create/get/stream/stop 始终路由到 live owner。请求落到非 owner 时只能返回持久化 snapshot 或明确提示当前无法继续实时订阅，不能启动第二个 producer。

## 2. 核心约束

1. 浏览器与 ChannelDelivery 是独立 subscriber，不是 producer owner；PersistSink
   已脱离 HTTP 生命周期，但仍由 producer 编排调用，迁移为独立 subscriber 是合入前待完成项。
2. 每个逻辑 run 使用稳定 `run_id` 与 `assistant_message_id`。
3. snapshot 与 subscribe 必须原子组合，不能先查历史再单独订阅。
4. 业务事件在 run 内使用严格递增 sequence；keepalive 不进入序列。
5. assistant 仍为 DB 单行；允许节流语义检查点，禁止逐 token 写库。
6. 临时错误与最终失败分开；`will_retry=true` 不结束 run。
7. 用户 stop 是显式操作；刷新与网络断开不得等同 stop。
8. 进程重启不自动重放可能产生副作用的工具。
9. run 创建必须幂等；消息与 run 在一个事务中落库，提交后才启动 producer。
10. 模型 retry 使用 attempt 隔离，越过工具/HITL 副作用边界后不得整步自动重试。
11. Persist、SSE、Channel 使用不同背压策略；所有队列和内存状态必须有界。

## 3. 组件与职责

```text
Browser → Run API → RunService → RunManager → QaService producer
                         │             ├─ snapshot + sequence buffer → SSE subscribers
                         │             └─ PersistSink checkpoints
                         └──────────────────────────────────────────→ PostgreSQL

Telegram / Automation → ChannelRunService → RunManager → RunOrchestrator
                                                ├─ PersistSink
                                                └─ bounded ChannelDelivery worker
```

Web 与通道都不依赖浏览器连接完成落库，并共享 RunManager 的 run 身份、资源上限与终态状态机。通道内部仍使用 headless orchestrator 做事件映射，平台发送由独立有界 worker 消费。

### RunManager

持有进程内 RunHandle、producer task、builder 快照、sequence、有限事件缓存、subscriber queue 与 cancel token。subscriber 为零时不取消 producer。

### RunService

处理用户/session 权限、同 session active run 冲突、API DTO 与 RunManager/Repository 编排。API 层不直接访问数据库。

### PersistSink

决定语义检查点与时间节流。run/assistant 终态使用 compare-and-set，防止 completed、stop、shutdown 相互覆盖；正文不会按 token 写库。

### RecoveryService

启动时查询无 live owner 的非终态 run，将其标记 interrupted；assistant 使用最近 parts 检查点收口为 partial/server_restart。

## 4. 调用与数据流

### 4.1 新建 run

```text
POST /api/chat/runs
  → 校验 Idempotency-Key/client_request_id 与 request digest
  → 校验 user/session/qa_type
  → 同 session active run 冲突检查
  → 单事务落库 user message + assistant skeleton + queued agent_run
  → 提交后 RunManager 启动 producer
  ← run_id + assistant_message_id + status
```

创建响应丢失时，客户端使用原幂等键重试，服务端返回原 run。相同键但请求摘要不同返回 409。user message、assistant skeleton 与 run 在同一事务中按 FK 顺序 flush；任一 flush、约束检查或 commit 失败都会整体回滚。若事务已提交但 producer 注册失败，服务端立即将 run/assistant 收口为 error；若进程在收口前退出，启动 recovery 将 queued run 收口为 interrupted。

返回 run 身份后，浏览器单独订阅：

```text
GET /api/chat/runs/{run_id}/stream?after_sequence=N
  → 鉴权
  → 原子注册 subscriber + 复制 snapshot
  → run-snapshot(snapshot_sequence=S)
  → events(sequence > S)
  → finish + [DONE]
```

旧 `/api/chat/sessions/stream` 直接删除，前后端只使用 run API，避免长期维护两条发送路径。

### 4.2 刷新与网络恢复

```text
浏览器断开
  → 删除 SseDelivery subscriber
  → producer/PersistSink/ChannelDelivery 继续

浏览器重新进入
  → 历史发现 streaming assistant / active run
  → GET run snapshot
  → 使用 replace 语义重建 parts
  → 从 snapshot_sequence 继续订阅
```

客户端发现 sequence gap 或未收到终态的 EOF 时，不把本轮标成功，而是重新查询 run 并重订阅。

重订阅采用指数退避、随机抖动和连续失败上限。达到上限后保留已有内容与非终态状态，并提供手动重连入口，避免服务恢复时大量页面同时高频请求。

### 4.3 用户停止

```text
POST /api/chat/runs/{run_id}/stop
  → 校验 run owner
  → RunManager cancel
  → RunAborted(user_stop)
  → run partial / assistant partial
  → finish_reason=stopped
```

`beforeunload` 不再调用 stop。

### 4.4 HITL

HITL pending 使用同一 run/message 身份：

```text
running → hitl_pending → running → terminal
```

pending 阶段 assistant 保持 streaming。若等待期间浏览器断开，pending 仍存在；重新进入后 snapshot 包含 HITL part。HITL resume 可重建执行 task，但不得创建新逻辑 run。

工具 part 以 `tool_call_id` 作为同一 assistant 内的稳定身份。刷新重放、HITL resume 或上游同时产生 tool start/input 事件时，服务端投影必须在原 part 上更新 input、HITL、output 与 status；不得追加同 ID 的第二个 part。客户端收到 snapshot 时同样按该身份归并，以清理此前已持久化的重复块。

LangGraph 在每个执行分段结束时都会产生 `[DONE]`。当分段因 HITL 暂停时，RunService
不得把该标记发布为整个 Run 的 `StreamDone`，原 SSE subscriber 应继续等待 resume 后的事件。
若原订阅已经因网络错误退出，审批接口返回 `running` snapshot 后，前端必须重新订阅同一
`run_id`。

前端同一 chat 页面只保留一个可写 UI 的 subscription。切换会话时递增本地 subscription
generation 并中止旧连接；旧 Run 继续在服务端运行，但旧连接的迟到事件因 generation 不匹配
而被丢弃。`currentRunId` 只能在当前 session 身份匹配时使用，HITL 审批优先读取目标 session
对应的持久化 run_id，禁止沿用上一会话的全局 run 身份。

HITL UI 状态按 `session_id` 保存，审批项同时绑定 `run_id` 与 `interrupt_id`。页面只渲染当前
session 的 pending 项；切换会话仅隐藏，不删除其它 session 的审批。切回时可直接显示本地项，
也可由权威 `run-snapshot` 覆盖恢复。一个 session 的 resume 或终态只清理自身审批状态。

审批接口返回的权威 snapshot 一旦进入 queued/running/retrying，当前会话立即恢复“正在继续生成”
提示；该提示不等待下一段正文 token。hitl_pending 显示审批面板，终态关闭生成提示。

## 5. 状态、数据与权限

### 5.1 Run 状态机

```text
queued ──▶ running ◀──────▶ retrying
             │
             ├────▶ hitl_pending ────▶ running
             │
             └────▶ completed | partial | error | interrupted
```

终态不可覆盖。用户 stop 映射 partial/stopped；服务重启映射 interrupted/server_restart；最终模型失败映射 error。

assistant 消息继续使用 `completed/partial/error` 终态。run interrupted 映射 assistant partial，避免扩大既有消息状态枚举。

### 5.2 PostgreSQL

run 记录至少保存：

- run/user/session/assistant 身份；
- qa_type、origin、owner_instance_id；
- status、last_sequence、finish_reason；
- error_code、用户安全错误文案；
- retry attempt/max；
- started/updated/finished 时间。

parts 快照只在完整工具结束、阶段结束、HITL pending 或节流条件满足时更新同一 assistant 行。

run 还需保存 `client_request_id`、请求摘要和当前 `attempt_id`。`(user_id, client_request_id)` 使用唯一约束；同键同摘要返回原 run，同键不同摘要返回冲突。

### 5.3 权限

创建、查询、订阅和停止 run 均验证 Cookie Session 用户与 run.user_id。按 session_id 或 run_id 猜测不能读取其它用户内容。通道入站先通过 ChannelBinding 确认 user/session，再创建 run。

## 6. 失败处理与可观测性

### 临时模型错误

临时错误发送：

```json
{
  "type": "run-status",
  "status": "retrying",
  "will_retry": true,
  "error_code": "MODEL_STREAM_DISCONNECTED",
  "message": "模型连接中断，正在重试",
  "attempt": 2,
  "max_attempts": 3
}
```

恢复后重新发送 running。只有重试耗尽才进入 error。tool error 继续使用现有 `tool-output-available` 双层语义。

Model Execution 是交互模型调用的唯一 owner：它记录 `model_attempt` 的可见 token、tool/HITL 副作用边界，只在边界未越过且错误可重试时发出 retry。正常响应的 `length_stop`、`safety_stop`、`partial_output` 和工具后的空终态也通过同一 `RuntimeOutcome` 表达；provider sampling retry 不与该路径叠加。

Context Lifecycle 在最终 `ModelRequest` 上只构造一次 `ContextSnapshot`，负责 dangling call normalization、compaction、可重建 context source 和最终预算判断。Tool Execution 在 dispatch 边界先分类 typed failure，再接收 DeepAgents `FilesystemMiddleware` 已处理的结果，只有无 backend 且仍未有界时才做一次 head/tail fallback。Run Governor 统一 loop、tool、subagent active/total/depth 限制；Runtime Telemetry 只读这些 outcome 和 snapshot，不参与决策。

### EventBus 背压

- PersistSink 不得静默丢失状态与终态；正文检查点可以合并为最新 snapshot。数据库持续不可写达到 persistence timeout 后，停止继续生成不可保存的内容。
- SseDelivery 达到队列事件数或字节数上限时，只断开慢订阅者，由客户端 snapshot 恢复。
- ChannelDelivery 使用事件批次数与字节数双上限；溢出或发送失败写入独立 `t_agent_delivery`，不阻塞 producer，也不改变 run 终态。P0 不保证进程退出后的平台出站续发。
- 事件发布不能顺序等待所有 Delivery；终态可靠落库后才成为权威结果。

### 工具 timeout 与取消

MCP async 工具使用统一执行 timeout，run stop 使用 cancel grace。用户 stop 只表示发出取消请求，不表示远程副作用已经撤销；仍在运行的 tool part 写 `outcome=unknown`，终态后的迟到结果不再修改消息。沙箱和 Web 工具继续使用各自已有 timeout。高风险且无幂等键或状态查询能力的工具不得自动重试。

### Delivery 失败

SSE 写失败只移除该 subscriber；Telegram 发送失败记录 delivery failure。两者均不把成功 run 改为 error，也不阻止 PersistSink 落库。

### 服务重启

启动 recovery 对悬空 run：

- 保留最近检查点；
- run → interrupted；
- assistant → partial/server_restart；
- running tool → error/结果未知；
- 不调用模型和工具。

若发现 `streaming` assistant 已没有对应 run（旧版本残留、人工误删或历史异常数据），recovery 保留已有 parts，将 assistant 收口为 `partial/server_restart`。这条修复只结束永久 loading，不删除用户消息。

run 数据已保存身份、owner、sequence、attempt 和错误字段；平台发送另存 delivery 状态，日志包含 run/delivery/error code。完整的 active run、队列字节、重连和回收指标尚未接入监控系统。

### 资源回收

active run 数量、单 run 时长、累计输出、event buffer、subscriber queue、HITL pending、terminal retention 和 shutdown drain 均使用配置上限。终态保留期结束后释放 RunHandle，后续查询读取 PostgreSQL snapshot。

## 7. 代码入口

当前入口：

- `backend/packages/noesis-core/src/noesis/domain/chat/runs/`：RunHandle、RunManager、状态机、snapshot。
- `backend/packages/noesis-core/src/noesis/domain/chat/runs/`：sequence、多 subscriber、缓存与慢消费者隔离。
- `backend/packages/noesis-core/src/noesis/services/qa/`：producer 装配与 PersistSink。
- `backend/packages/noesis-core/src/noesis/services/run_service.py`：run 应用服务。
- `backend/server/api/chat_api.py`：`/api/chat/runs*`。
- `backend/packages/noesis-core/src/noesis/storage/postgres/models/chat.py`：run ORM。
- `frontend/src/api/chat.ts`：run API。
- `frontend/src/views/chat/useSSEStream.ts`：snapshot、sequence、重订阅。
- `frontend/src/store/business/initChatHistory.ts`：active run 恢复。

## 8. 验证方式

至少覆盖：

1. SSE 断开后 producer 继续并 completed。
2. 两个 subscriber 中一个断开，另一个完整收到终态。
3. snapshot 与并发 delta 无丢失、无重复投影。
4. sequence gap 触发 snapshot replace。
5. 无终态 EOF 不误判成功。
6. 刷新后恢复同一 assistant_message_id。
7. 明确 stop → partial/stopped，刷新不 stop。
8. retrying 保持 loading，恢复后 completed，耗尽后 error。
9. 后端重启悬空 run → interrupted/partial，工具不重放。
10. HITL pending 断线后仍可 resume。
11. Telegram 无网页订阅仍落库；投递失败不污染 run。
12. 数据库写入次数证明没有逐 token UPDATE。
13. 创建 ACK 丢失后使用原幂等键重试，不产生重复消息或 producer。
14. 模型新 attempt 不拼接旧正文，工具开始后不整步自动重试。
15. 慢 SSE 溢出只断开自身；PersistSink 仍可靠终态。
16. PostgreSQL 持续不可写时有界收口，不无界缓存。
17. 工具取消未确认时显示 unknown，迟到结果不覆盖终态。
18. terminal/HITL 到期后内存可回收，数据库查询仍返回结果。
19. 连续断线采用退避和抖动，不产生重连风暴。

## 9. 已知限制

- P0 RunManager 为进程内组件，不支持任意 backend replica 接管 live run。
- Python 进程崩溃后只恢复持久化检查点，不恢复内存 token buffer。
- 同 session 默认只允许一个 active run，不提供 queue/steer。
- Channel outbound 仍是进程内有界队列，不是 durable spool；进程重启后只把未完成 delivery 标记 lost，不自动续发。
- Web Run 的 PersistSink 已独立于浏览器连接，但检查点消费仍在 producer 编排协程内，尚未注册为 RunManager 独立 subscriber；完成 OpenSpec 任务 3.2 前不得归档本变更。
- 完整运行指标尚未接入监控系统。
- P0 工具仅在已有适配能力时使用幂等键、取消和状态查询；不具备能力时只能报告 unknown outcome。
- PostgreSQL 持续不可用时无法同时保证继续生成与可靠保存，本设计选择有界停止。
- 多标签页可以同时订阅，但 P0 不提供跨标签页通知协调。
- 前后端必须同版本发布；旧客户端不能继续调用已删除的 `/sessions/stream`。

## 10. 发布与回滚

- 当前 `deploy/docker-compose.yml` 只有一个 backend 实例，符合进程内 live owner 约束。若后续扩容，必须先增加 owner sticky routing 或外部 owner registry。
- Nginx `/api/` 的 `proxy_read_timeout` 为 600 秒，后端 SSE keepalive 默认 25 秒；外层网关也必须让 read timeout 大于 keepalive 间隔，并关闭响应缓冲。
- 发布前先停止接收新 run，等待 active run 在 shutdown drain 时间内完成；超时任务由 shutdown 取消，下一次启动 recovery 收口为 `interrupted/server_restart`。
- 前后端需要一起发布。回滚应用时保留 `t_agent_run` 表和 migration，不恢复已删除的旧发送、停止或 resume 接口；若旧前端仍在缓存中，应先阻止发布而不是维持双 API。

## 11. 关联资料

- [OpenSpec proposal](../../../openspec/changes/durable-agent-run-recovery/proposal.md)
- [OpenSpec design](../../../openspec/changes/durable-agent-run-recovery/design.md)
- [agent-run-recovery spec](../../../openspec/changes/durable-agent-run-recovery/specs/agent-run-recovery/spec.md)
- [当前 SSE 架构](chat-streaming.md)
- Codex：`/Users/zzq/Desktop/code/codex`，commit `95637f7`
- OpenClaw：`/Users/zzq/Desktop/code/openclaw`，commit `8ecb609`
