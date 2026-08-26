## Why

Noesis 已有持久化 Run、独立 SSE 订阅、sequence 和 snapshot，但当前 projection 更新与 sequence 分配不原子，Web 路径还存在内部 SSE 文本往返和重复 EventBus。这会导致 snapshot revision 错位、断线恢复不稳定，且新 Tab 无法只依赖服务端事实发现 active Run。

## What Changes

- 重构 `/api/chat` 下的 Web Agent Run 管线，使 typed `RunEvent` 只经过一次 mapping，并由 `RunHandle` 单写入边界原子更新 sequence、projection、snapshot 和 subscriber buffer。
- 使检查点只写入与确定 sequence 绑定的 immutable snapshot；中间检查点可合并，终态必须先与 assistant 消息同事务落库，再对 Delivery 可见。
- 新增 `GET /api/chat/sessions/{session_id}/active-run`，使新 Tab、刷新页和断线恢复可从服务端获得权威 Run snapshot，不依赖其它 Tab 的 `sessionStorage`。
- 固化多 Tab 语义：每个 Tab 独立订阅同一 Run；断开任意 Tab 不取消 producer；任意 Tab 的 stop 或 HITL resume 只能作用于当前用户所属 Run。
- 使 SSE 慢消费者、sequence gap、无终态 EOF 和持久化失败都有可验收的恢复与错误语义，不伪造 completed/partial/error 终态。
- 使生产 lifespan 在 recovery 和后台 runtime 启动前持有 PostgreSQL advisory lock，第二个 Uvicorn worker 或 backend 实例必须 fail-fast。本次不引入 Redis Pub/Sub、owner lease 或跨进程 event transport。
- **BREAKING**：前后端与数据库 schema 作为一个发布单元切换；不保留旧 EventBus、内部 SSE parser、双写、双读或旧客户端协议 adapter。
- `TEST_CASE_QA`、`CaseCoordinator`、`phase-*` 事件、`/api/chat/runs/{run_id}/test-case/resume` 与测试用例导出不进入本 change；本次不迁移、不扩展、不为其增加回归要求。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `platform-chat`：增加服务端 active Run 发现、多 Tab 恢复、原子 snapshot/sequence、终态持久化屏障和单次协议切换要求。
- `agent-delivery`：收敛 typed event 主路径，区分 SSE subscriber 与 PersistWriter 的背压策略，并固化多 subscriber、gap recovery 与终态可见性。
- `container-deployment`：将单 active backend 从部署约定提升为 PostgreSQL advisory lock 强制的可验收启动约束。

## Impact

- 后端：`domain/chat/runs/`、`domain/chat/delivery/`、`domain/chat/streaming/`、`services/run_service.py`、`services/qa/`、`repositories/agent_run_repository.py`、`server/api/chat_api.py`、`server/main.py`。
- 前端：`frontend/src/views/chat/useSSEStream.ts`、`frontend/src/store/business/initChatHistory.ts`、`frontend/src/views/chat.vue`、`frontend/src/api/chat.ts`。
- API：保留 `/api/chat/runs`、`/api/chat/runs/{run_id}`、`/stream`、`/stop`、`/hitl/resume`；新增 `/api/chat/sessions/{session_id}/active-run`。
- 数据：Run checkpoint 与 terminal transaction 必须同时写入 `snapshot` 与 `last_sequence`；不新增 token event log。
- 部署：只允许一个 active backend；现有 active Run 在发布窗口 drain 后仍未完成时收口为 `interrupted`。
- 研究依据：`/Users/zzq/Library/Mobile Documents/iCloud~md~obsidian/Documents/knowledge-base/Interview/highlights/SSE/design/reliable-sse-multitab-refactor.md`（实现稳定后归档回 `docs/research/sse/`）。
