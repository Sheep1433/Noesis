## Context

当前 `RunOrchestrator.stream_sse()` 在 HTTP generator 内创建 producer；SSE consumer 退出时 `finally` 会取消 producer，`useSSEStream.ts` 还会在 `beforeunload` 调用 stop。虽然 RunEvent、PersistSink、SseDelivery 与 ChannelDelivery 已有分层，但网页连接仍实际拥有 run 生命周期，导致刷新、短暂断网、代理重连和多标签页无法重新加入正在运行的任务。

本设计参考并核对了以下实现：

- OpenAI Codex `95637f7056835fea66bdd0044414af480fc0fd74`：connection 只订阅 thread；`thread/resume` 将持久化 history 与 active turn snapshot 合并，并在同一 listener 顺序内订阅后续事件。进程重启后无 live turn 的 `InProgress` 历史会归一化为 `Interrupted`。
- OpenClaw `8ecb609990ff191bb9258f5685f90cbdde7e6c17`：`chat.send` 先返回 `runId/status=started`，随后 detached dispatch；WebSocket 事件带 sequence，客户端发现 gap 后重连并通过 history/startup 权威快照恢复。仅对满足幂等与安全条件的请求做受限 restart retry。

Noesis 的约束：浏览器继续使用 SSE；PostgreSQL 是消息与 run 元数据的 SSOT；流式正文不得按 token 高频写库；HITL 必须续写同一 assistant；四种 `qa_type` 均需共享生命周期语义；P0 不引入分布式任务系统。

## Goals / Non-Goals

**Goals:**

- 同一后端进程内，浏览器刷新、网络断开或某个 Delivery 失败不取消 Agent run。
- 使用稳定 run 身份、明确状态机和单调 sequence，支持多订阅与无缝重加入。
- 通过原子 snapshot + subscribe 消除“读完快照、尚未订阅”窗口内的事件丢失。
- 将临时模型异常、自动重试、用户停止、客户端断开、最终失败和服务重启分开表达。
- 保持 assistant 单行身份、HITL resume 和现有 SSE 事件兼容性。
- 后端重启后将悬空 run 确定性收口为 interrupted/partial，避免永久 streaming。
- 网页、持久化与消息通道消费同一 run，不因某个客户端状态相互取消。
- 创建请求可安全重试，模型 attempt 不产生重复正文或重复工具副作用。
- 慢消费者、工具取消与终态资源回收具有明确上限和可观测行为。

**Non-Goals:**

- 不保证 Python 进程崩溃后恢复原 coroutine 或 LangGraph 节点现场。
- 不自动重放已经开始工具调用、HITL、子 Agent 或其它可能产生外部副作用的 run。
- 不提供跨后端实例的实时订阅、leader election 或 worker takeover。
- 不用 WebSocket 替换 SSE，不在本变更内重做 Telegram ingress spool。
- 不改变 `agent-tool-failure-handling` 的 tool `status/outcome` 双层语义。

## Decisions

### 1. Run 生命周期由 RunManager 拥有，HTTP 只拥有 subscription

新增 `RunManager`（建议位于 `backend/noesis_server/domain/chat/runs/`）管理进程内 `RunHandle`：

```text
RunHandle
  run_id
  user_id / session_id / assistant_message_id
  status
  producer_task
  builder / latest_snapshot
  next_sequence
  bounded_event_buffer
  subscribers
  cancel_token
  terminal_future
```

`QaService` 负责准备 Agent、插入 assistant skeleton，并把 producer coroutine 交给 RunManager。`SseDelivery` 只注册/释放 subscriber；subscriber 为零时 producer 继续运行。只有以下事件允许取消 producer：

- 用户调用 stop；
- HITL reject/timeout 的既有终态路径；
- 服务进程主动 shutdown；
- 系统明确的 run timeout 或管理员取消。

**替代方案：继续由 StreamingResponse generator 持有 task。** 改动较小，但无法满足断线继续，拒绝。

**替代方案：立即引入 Celery/Temporal。** 可跨进程，但会扩大部署、checkpoint、工具副作用和 HITL 范围，P0 不采用。

### 2. 使用 JSON 启动 + 独立 SSE 订阅 API

新增 `/api/chat` 端点：

```text
POST /api/chat/runs
GET  /api/chat/runs/{run_id}
GET  /api/chat/runs/{run_id}/stream?after_sequence=<n>
POST /api/chat/runs/{run_id}/stop
```

- `POST /runs` 完成鉴权、session 校验、user message 落库、assistant skeleton 和 run 注册后返回 `run_id`、`assistant_message_id`、`status=queued|running`。
- `GET /runs/{run_id}` 返回 run 状态、当前 `content.parts` 快照、`snapshot_sequence`、finish/error/retry 元数据。
- `GET /runs/{run_id}/stream` 先发 `run-snapshot`，随后发送 sequence 大于 snapshot 的实时事件。
- `POST /runs/{run_id}/stop` 是唯一网页用户停止入口，要求 run 属于当前用户且仍可取消。
- 删除现有 `POST /api/chat/sessions/stream`，前后端只保留 run API 一条发送路径。

API 层仍通过 Service，错误响应使用 `ResponseUtil`。SSE 建连前的鉴权/参数错误使用 HTTP 4xx；建连后的运行错误使用 RunEvent。

**替代方案：在现有 POST SSE 上增加 reconnect 参数。** POST 同时承担创建和订阅，难以幂等区分“新建 run”与“加入旧 run”，拒绝。

### 3. sequence 属于 run；恢复采用原子 snapshot + subscribe

每个对客户端有状态影响的 RunEvent 在 RunManager 内分配严格递增 `sequence`。keepalive 不分配 sequence。RunManager 在同一 async lock 下完成：

1. 注册 subscriber queue；
2. 复制 builder/current snapshot；
3. 读取当前 `last_sequence`；
4. 返回 snapshot；
5. 释放 lock，开始投递 `sequence > snapshot_sequence` 的事件。

这样不会发生 snapshot 与 live subscription 之间的丢帧。事件 buffer 只保留有界窗口，用于短时间补发；buffer 不作为消息 SSOT。

当客户端传入 `after_sequence`：

- 能从 buffer 连续补齐时，先补发缺失事件；
- 不能补齐或 run 已被进程重建时，服务端发权威 `run-snapshot`，客户端替换本地 run parts；
- 客户端发现 sequence gap 时不得把 EOF 当成功，必须重新查询/订阅。

**替代方案：永久持久化每个 token event。** 写放大高、恢复仍需 projection，当前规模不采用。

### 4. PostgreSQL 持久化 run 元数据与节流快照，不按 token 写正文

新增 `agent_run`（命名以 migration 最终选择为准）记录：

```text
run_id (PK)
user_id / session_id / assistant_message_id
qa_type / origin
status
last_sequence
finish_reason
error_code / user_error_message
retry_attempt / retry_max
started_at / updated_at / finished_at
owner_instance_id
```

运行时 builder 仍是实时快照权威；assistant message 仍是一行。为避免进程崩溃后只剩空 skeleton，允许 PersistSink 在以下条件写入**节流检查点**：

- 完整 tool end、HITL pending、阶段结束等语义边界；或
- 距上次正文检查点超过可配置间隔且内容有变化。

检查点更新同一 assistant 行的 `content.parts`，必须节流，禁止逐 token UPDATE。终态仍只允许一次 compare-and-set 更新。现有只写会话 context 的 `_persist_stream_checkpoint` 将被收敛到 PersistSink。

**替代方案：完全不持久化中间正文。** 同进程刷新可恢复，但进程崩溃后用户看不到已生成内容；不采用。

### 5. run 状态与 assistant 消息状态分层

Run 状态：

```text
queued
running
retrying
hitl_pending
completed
partial
error
interrupted
```

转换约束：

```text
queued → running
running ↔ retrying
running → hitl_pending → running
queued|running|retrying|hitl_pending → completed|partial|error|interrupted
terminal → 不可再转换
```

- 用户 stop → run `partial`，assistant `partial`，`finish_reason=stopped`。
- 最终模型/系统失败 → run `error`，assistant `error`。
- 后端重启发现旧 owner 的非终态 run → run `interrupted`，assistant `partial`，`finish_reason=server_restart`。
- HITL pending 保持 assistant `streaming`，resume 使用同一 run_id 与 assistant_message_id；若现有 LangGraph resume 必须重建 task，也仍属于同一个逻辑 RunHandle。

assistant 不新增 `interrupted` status，避免扩大现有消息枚举；区别通过 run status 与 `finish_reason` 表达。

### 6. 临时错误使用 run-status，不沿用终态 error

新增 SSE/RunEvent：

```json
{
  "type": "run-status",
  "run_id": "...",
  "status": "retrying",
  "sequence": 42,
  "will_retry": true,
  "error_code": "MODEL_STREAM_DISCONNECTED",
  "message": "模型连接中断，正在重试",
  "attempt": 2,
  "max_attempts": 3
}
```

规则：

- `will_retry=true` 不结束流、不将 assistant 标为 error。
- 恢复后发 `status=running`。
- 重试耗尽后才发终态 `error` + `finish`/`[DONE]`。
- 用户可见 message 使用受控错误码映射，不透出内部异常、路径或凭据。
- 现有 tool error 继续走 `tool-output-available`，不得混入 run retry。

### 7. 启动恢复只收口，不自动重放副作用

应用启动时由 recovery service 查询非终态 `agent_run`：

- 若 `owner_instance_id` 不属于当前活跃实例，标记 `interrupted`；
- 将 assistant 当前检查点收口为 `partial`，追加结构化 `finish_reason=server_restart`，不伪装成用户 stop；
- running tool part 标记为 error/unknown result，用户文案说明服务重启导致结果未确认；
- HITL pending 若 LangGraph checkpoint 与 pending token 均可验证，MAY 保持 `hitl_pending`；否则 interrupted；
- 不自动调用任何工具或模型。

未来若增加 restart retry，必须另开 change，定义幂等键、claim、工具副作用分类和重复执行防护。

### 8. 前端按 run 身份恢复，不以 EOF 判成功

`useSSEStream.ts` 调整为 run client：

- 发送先创建 run，再订阅；保存当前 `run_id/assistant_message_id/last_sequence`。
- `beforeunload` 只释放浏览器资源，不发 stop beacon。
- 收到 `run-snapshot` 时按 snapshot 替换当前 assistant parts。
- 收到连续事件时 reduce；发现 gap、网络错误或无终态 EOF 时查询 run 并重订阅。
- 只有 `completed/partial/error/interrupted` 或兼容 `finish` 才结束 loading。
- `retrying` 显示“正在重试”，不弹最终错误；`interrupted` 显示“服务重启，本轮已中断”。
- 历史初始化若发现 assistant `streaming`，查询关联 run：活跃则订阅；不存在则等待服务端 recovery 收口后刷新一次。

### 9. ChannelDelivery 与网页订阅共享 producer，失败相互隔离

RunManager 为 PersistSink、SseDelivery 和 ChannelDelivery 注册独立队列。任一 Delivery 写失败：

- 仅移除/重试该 Delivery；
- 不取消 producer；
- 不改变 Agent run 成功状态；
- 平台投递结果另记 delivery 日志/状态，不污染 run terminal outcome。

无浏览器订阅时 PersistSink 与 ChannelDelivery 继续工作。P0 不要求通道在网页发起的 run 上自动镜像，仍按 binding/origin 策略决定订阅。

### 10. run 创建使用幂等键与单事务持久化

新客户端创建 run 时必须提交稳定 `client_request_id`（HTTP 可映射为 `Idempotency-Key`）。服务端对 `(user_id, client_request_id)` 建立唯一约束，并在一个数据库事务中完成：

1. 校验 session 与 active run；
2. 插入 user message；
3. 插入 assistant skeleton；
4. 插入 queued `agent_run`；
5. 提交后才注册并启动 producer。

相同用户使用相同幂等键和相同请求摘要重试时返回原 `run_id`；请求摘要不同则返回 409。事务提交后、producer 启动前若进程退出，启动 recovery 将 queued run 收口为 interrupted，不产生重复消息。

### 11. 模型重试按 attempt 隔离投影

每次模型调用使用单调递增的 `attempt_id`，相关 text/reasoning/tool proposal 事件均携带该身份。重试策略按副作用边界决定：

- 尚未产生用户可见正文且未开始工具：允许自动重试；
- 已产生正文但未开始工具：只有 projection 支持撤销当前 attempt 未确认片段时才允许替换后重试，否则收口为 partial/error；
- 已开始任何工具、HITL 或子 Agent：不得自动重试整个模型步骤；
- 不同 attempt 的 delta 不得直接追加到同一未分段正文；旧 attempt 的迟到事件必须丢弃并记录。

快照记录当前 `attempt_id` 与已确认 parts。`retrying` 事件说明是“重新连接”还是“重新生成”；前端先应用服务端 snapshot，再接收新 attempt，不能自行猜测截断位置。

### 12. EventBus 按订阅类型实施背压

队列同时受事件数与估算字节数限制，不能只有事件数上限：

- PersistSink 是可靠消费者，不允许静默丢弃状态事件；正文可由 builder 合并为最新节流快照，终态必须直接进入可靠 compare-and-set 持久化路径。持续数据库失败超过 run persistence timeout 时，run 进入 error/partial，不能继续生成不可保存的无限内容。
- SseDelivery 队列溢出时断开该慢订阅者并记录原因；客户端随后通过 snapshot 恢复，不阻塞 producer。
- ChannelDelivery 队列溢出或发送失败只记录独立 delivery failure；P0 不承诺进程重启后的 durable outbound。

事件发布不得顺序等待所有 Delivery。终态持久化完成后才可把 run 视为 authoritative terminal；SSE/Channel 的终态发送失败不回滚 Agent 结果。

### 13. 工具 timeout、取消与迟到结果分开表达

用户 stop 先把 run 标记为 `cancel_requested` 元数据并触发 cancel token，不立即假设外部工具已经停止。工具适配层必须支持：

- 可配置执行 timeout 与 cancel grace period；
- 本地子进程和可取消 HTTP/MCP 请求的资源清理；
- `tool_call_id` 与可选外部 operation/idempotency key；
- 取消或超时后的迟到结果丢弃，不得改变已确定的 run 终态；
- 无法确认外部副作用时记录 `outcome=unknown`，不得推断成功或自动重放。

grace period 结束后 run 可收口 partial/stopped，但工具 part 必须显示“取消结果未确认”。高风险工具若不支持幂等或状态查询，不允许自动重试。

### 14. RunManager 使用有界保留与回收

配置项至少包含 active run 上限、单 run 最长时间、event buffer 事件/字节上限、subscriber 队列上限、terminal 内存保留时间、HITL pending 过期时间与 shutdown drain 时间。终态写库且保留期结束后，RunManager 删除 task、builder、buffer、subscriber 和 terminal future；后续查询完全读取 PostgreSQL snapshot。

超过用户级并发、输出长度或事件量限制时，系统拒绝创建或以明确 `limit_exceeded` 终止，不能无限占用内存。HITL 过期按既有策略收口，重复审批必须幂等。

### 15. 客户端重连必须限速

无终态 EOF、网络失败和 sequence gap 使用指数退避与随机抖动重连，并设置单次连接超时和最大连续失败次数。达到前台自动重试上限后仍保留 active 状态，显示手动重连入口；页面重新可见或网络恢复时允许再次查询权威状态。多个标签页可各自订阅，但通知和错误 UI 只影响本地页面。

## Risks / Trade-offs

- [进程内 RunManager 无法跨实例] → P0 明确要求同一实例；部署保持单 active backend 或 sticky routing。多实例执行另开 change。
- [检查点增加数据库写入] → 只在语义边界或可配置节流间隔写；增加写入次数与性能测试。
- [snapshot 与 delta 重复导致 UI 重复文本] → snapshot 使用 replace 语义，delta 按 sequence 去重；前端不得 append 小于等于 last_sequence 的事件。
- [旧客户端无法继续发送] → 本变更直接删除 `/sessions/stream`，前后端必须同版本发布，并在发布前结束 active run。
- [同 session 并发 run 破坏 LangGraph/thread 语义] → P0 默认每个 session 最多一个非终态 run；重复发送走明确冲突或后续 queue/steer change。
- [服务重启时工具实际已成功但没有结果] → 统一标 interrupted/结果未知，不自动重放；高风险工具不作成功推断。
- [HITL 长时间 pending 占用内存] → producer task 可结束但 RunHandle 保留可恢复状态；pending 数据以现有 checkpoint/pending store 为准。
- [终态事件与取消竞态] → PersistSink 使用数据库 compare-and-set；terminal future 和 run 状态机只接受首个合法终态。
- [创建响应丢失导致重复 run] → 幂等键、请求摘要和数据库唯一约束返回原 run。
- [模型流重试导致重复文本或工具] → attempt 隔离；越过工具/HITL 副作用边界后禁止整步自动重试。
- [慢订阅者阻塞 producer 或丢终态] → 按 Sink 类型实施背压，SSE 溢出断开恢复，终态走可靠持久化路径。
- [数据库长期不可写但模型继续产生内容] → persistence timeout 后停止 run 并暴露受控错误，禁止无界内存缓存。
- [取消请求未停止远程副作用] → 显式记录 cancel_requested、grace period 与 unknown outcome，不把任务取消等同外部操作撤销。
- [RunHandle/HITL 长期滞留] → 配置资源上限、过期与 terminal 回收，并对回收行为增加指标。
- [大量客户端同时重连] → 指数退避、随机抖动和服务端连接/订阅限额。

## Migration Plan

1. 增加 run 表/字段、幂等唯一约束、repository 与启动 recovery；部署时旧消息不需要回填 run。
2. 引入 RunManager、sequence、snapshot、Sink 背压、资源回收与终态 CAS，先用 headless/backend tests 验证。
3. 新增 `/api/chat/runs*` API，并删除 `/sessions/stream`。
4. 迁移 `QaService`，去除 generator disconnect 取消 producer 与重复断连 partial 分支。
5. 迁移前端 create/subscribe/resync，移除 `beforeunload` stop beacon。
6. 接入 attempt-aware run-status retrying，并补齐错误码、投影规则与用户文案。
7. 让 ChannelDelivery 订阅 RunManager；验证 Telegram 与无浏览器订阅场景。
8. 更新 `docs/architecture/platform/chat-streaming.md` 为 Current，并移除本设计文档的 Proposed 标记。

前后端必须同版本发布。回滚到旧版本前必须先 drain active run，或将其收口为 interrupted；旧版本无法继续新版本创建的 active run。

## Open Questions

- assistant 正文检查点的默认节流间隔与最大写入频率，需要通过本地压测决定，不在 spec 硬编码。
- P0 是否允许同 session queue 第二条用户消息，还是统一返回 409；本设计默认 409，后续可扩展 queue/steer。
- HITL pending 跨进程恢复是否已有足够持久化信息，需要实现阶段按 LangGraph PostgreSQL checkpoint 做验证；不足时按 interrupted 收口。
- 生产未来启用多 backend replica 时，RunManager 应迁移到专用 worker + Redis/PostgreSQL ownership，还是保持 sticky single owner，需要单独容量设计。
- 检查点持续失败时 `persistence timeout` 的默认值，以及已有正文应映射为 partial 还是 error，需要结合现有消息展示测试确定。
- 各类工具是否支持 cancel、幂等键和外部状态查询，需要建立 capability 清单；不具备能力的工具统一按 unknown outcome 处理。
