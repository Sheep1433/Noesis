## 1. 确定性失败测试与契约基线

- [x] 1.1 在 `backend/tests/test_run_manager.py` 用 barrier 稳定复现 projection 已包含事件 N 但 `snapshot_sequence=N-1` 的 apply/subscribe 竞态
- [x] 1.2 在 repository/service 测试中复现 checkpoint N 写入 N+1 projection 与迟到 checkpoint 覆盖新 sequence 的问题
- [x] 1.3 增加 terminal 先对 Delivery 可见、后持久化失败的回归测试，证明现有路径可伪造终态
- [x] 1.4 增加旧 producer segment 与旧 model attempt 在 resume/retry 后发送迟到 delta 的失败测试
- [x] 1.5 为最终 typed `RunEvent`、RunSnapshot、SSE frame、409、429 和 503 响应建立契约 fixture，不创建旧协议 fixture 或兼容 parser 测试

## 2. RunHandle 单写入边界

- [x] 2.1 在 `domain/chat/runs/manager.py` 将 sequence、projection、status、replay buffer 和 subscriber 注册收敛到 RunHandle 的同一 `asyncio.Lock`
- [x] 2.2 实现 `apply_event()`，在无 I/O await 的临界区内检查状态/generation/attempt、分配 sequence、reduce projection 并构建 immutable envelope
- [x] 2.3 使 `subscribe()` 在同一 lock 内先注册 bounded queue，再复制 snapshot 与可连续 replay，不连续时只返回 snapshot
- [x] 2.4 增加进程内 `producer_generation`，每次初始/HITL resume producer 启动前递增，并在 projection reduce 前拒绝迟到 generation
- [x] 2.5 保留独立 model `attempt_id`，仅在真正新 model attempt 时递增，并在 projection reduce 前拒绝迟到 attempt
- [x] 2.6 使 stop 只设置一次 `cancel_requested`、cancel producer 一次，重复 stop 等待同一 terminal completion
- [x] 2.7 使非终态 RunHandle 不受 retention/内存回收影响，只有 terminal 可靠落库后才进入定时回收
- [x] 2.8 运行 RunManager 单元测试，确认 N/N-1、snapshot/replay、stale generation/attempt、stop 幂等和 retention 全部通过

## 3. PersistWriter 与终态持久化

- [x] 3.1 定义携带 `snapshot_sequence`、immutable snapshot、kind 和可选 completion 的 `CheckpointRequest`
- [x] 3.2 实现每 Run 单槽 `pending_checkpoint + wakeup` 合并，仅保留待写 sequence 最大 snapshot，semantic checkpoint 立即唤醒
- [x] 3.3 修改 `AgentRunRepository` checkpoint，在同一事务中更新 run snapshot/last_sequence/metadata 与 assistant content，并用 stored sequence 条件防止回退
- [x] 3.4 实现 RunHandle `finalize()` terminal candidate、run + assistant compare-and-set transaction 和 `Committed | AlreadyFinalized | Failed` 结果
- [x] 3.5 使 terminal 只在 transaction committed 后切换 live projection、写 replay buffer、fan-out terminal 并发送 `[DONE]`
- [x] 3.6 实现 persistence blocked：超过 budget 后停止 producer，不发布伪终态，每 Run 最多保留一个 immutable terminal candidate 低频重试
- [x] 3.7 增加 checkpoint 合并数、延迟、失败、sequence lag、terminal CAS loser 和 persistence blocked 指标/关联日志
- [x] 3.8 运行持久化故障注入测试，覆盖短暂失败、持续失败、terminal 抢占 pending checkpoint 与 CAS loser

## 4. Typed event 主路径收敛

- [x] 4.1 在 `domain/chat/streaming/` 实现无状态 `RuntimeEventMapper`，直接将 LangGraph/LangChain raw event 转为封闭 typed `RunEvent`
- [x] 4.2 将 `LangGraphSseBridge` 中属于 `COMMON_QA`、`FAULT_OPERATION_QA`、`SUPER_AGENT_QA` 的状态提取迁入 mapper，未知事件记录并丢弃
- [x] 4.3 使 Web Agent Run 路径改为 raw event → mapper → RunHandle → SseDelivery/PersistWriter，删除内部 SSE encode/parse roundtrip 和 Web RunEventBus
- [x] 4.4 迁移 Telegram、飞书、cron 和 eval 中属于目标 qa_type 的调用方，使 headless Run 无浏览器仍能持久化和独立 Delivery
- [x] 4.5 在所有目标调用方迁移后删除与 RunHandle 重复的 RunLifecycle/RunOrchestrator fan-out、active stream registry 和旧 disconnect 即 partial 逻辑
- [x] 4.6 审查本 change diff，确认未迁移或扩展 `TEST_CASE_QA`、CaseCoordinator、`phase-*`、test-case resume/export，且新主路径不为它们保留兼容 parser
- [x] 4.7 运行 Web、Channel、HITL、无浏览器持久化和 Delivery failure 契约测试，确认 raw event 只经过一次 mapping

## 5. `/api/chat` active Run、鉴权与配额

- [x] 5.1 在 repository 和 `RunService` 实现按 `(session_id, current_user_id)` 查询 active Run 并返回完整 RunSnapshot
- [x] 5.2 在 `server/api/chat_api.py` 新增 `GET /api/chat/sessions/{session_id}/active-run`，使用 `ResponseUtil`，对未知/已删除/跨用户 session 统一返回 404
- [x] 5.3 固定 `POST /api/chat/runs` 的 409 schema，只对当前用户暴露已有 `run_id`、`assistant_message_id`、`session_id` 和 status
- [x] 5.4 使 get/stream/stop/HITL resume 统一按 `(run_id, current_user_id)` 鉴权，并防止旧 run_id 命令作用于同 session 的后续 Run
- [x] 5.5 实现单 Run、单用户和全局 SSE subscription 配额，在建立 stream 前以 429/`SSE_SUBSCRIPTION_LIMIT` 拒绝超额请求
- [x] 5.6 当数据库 Run 非终态但本地无 RunHandle 时，stream 在开流前返回 503/`RUN_OWNER_UNAVAILABLE`，不创建第二 producer
- [x] 5.7 增加 API 集成测试，覆盖 active-run null/snapshot、409 join、429 quota、503 owner、stop/HITL 幂等和全部跨用户拒绝

## 6. 前端多 Tab 与恢复状态机

- [x] 6.1 在 `frontend/src/api/chat.ts` 增加 active-run API 与最终 RunSnapshot/409/429/503 类型
- [x] 6.2 使 `chat.vue` 进入 session 时并行加载 messages、attachments 和 active Run，active snapshot 以 `assistant_message_id` replace 历史 streaming assistant
- [x] 6.3 重构 `useSSEStream.ts` 为 Discovering → SnapshotReplace → Subscribing → Applying → GapRecovery/Disconnected/Done 状态机
- [x] 6.4 实现 sequence 去重与 gap 规则，无终态 EOF/网络恢复/页面 visible 使用带抖动退避的 snapshot recovery，旧 subscription generation 迟到响应直接丢弃
- [x] 6.5 使 `sessionStorage` 只作为当前 Tab 提示，移除从它或消息历史推测 active Run 的必要性
- [x] 6.6 处理 409 join：当冲突 Run 属于当前 session 时查询 snapshot 并订阅，不展示为普通发送失败
- [x] 6.7 更新前端单元测试，覆盖 snapshot replace、duplicate/gap、EOF recovery、跨 session generation 隔离、409 join 和 stop/HITL 多 Tab 语义
- [x] 6.8 用一个 BrowserContext 的两个 Page 新增 Playwright E2E：中途加入、关闭创建 Tab、单 Tab 断网恢复、任意 Tab stop、任意 Tab HITL resume 与相同终态

## 7. 单 active backend 与可观测容量

- [x] 7.1 在 PostgreSQL manager 提供 lifespan 专用 advisory lock 连接，lock key 固定且不与业务 transaction 共用连接
- [x] 7.2 调整 `server/main.py` lifespan 顺序：先获取 lock，再 migration/verify、recovery、checkpointer/KB、scheduler/channel runtime；未获取 lock 不执行 recovery 或后台 runtime
- [x] 7.3 监测 advisory lock 专用连接；连接丢失时使实例退出或 not-ready 并停止接收新 Run
- [x] 7.4 使 Docker、裸机命令与部署文档固定单 worker，并说明 advisory lock 是多容器/多进程的最终保护
- [x] 7.5 增加 lifespan 测试，覆盖第二 worker/容器 fail-fast、未持 lock 不 recovery、连接丢失和正常退出释放 lock
- [x] 7.6 增加 active Run/subscription、event-loop lag、event-to-client latency、replay/subscriber bytes、overflow、checkpoint lag、persistence blocked 和 terminal retention 指标
- [x] 7.7 审查目标路径中的同步 HTTP、`time.sleep`、subprocess wait、大文件和高成本 deepcopy，将 event loop 上的阻塞工作移入线程/进程池或独立服务
- [x] 7.8 执行 100 active Run、每 Run 2–3 Tab、10–30 events/s、混入慢消费与重连的压测，保留 p50/p95/p99 latency、event-loop lag、RSS、queue bytes、checkpoint lag 和回收数据

## 8. 最终验证、发布与文档

- [x] 8.1 运行后端 RunManager、repository、Run API、recovery、HITL、Channel 和 lifespan 相关测试，再运行 `cd backend && uv run pytest tests/ -q`
- [x] 8.2 运行前端 SSE/chat 单元测试、Playwright 双 Tab E2E、`pnpm lint` 和 `pnpm build`
- [x] 8.3 执行一次端到端故障矩阵：单 Tab 关闭/慢消费/断网、sequence gap、无终态 EOF、PostgreSQL 短暂/持续失败、terminal CAS loser、backend restart 和第二 worker fail-fast
- [x] 8.4 使用 `code-review` 同时检查仓库规范与本 change spec，重点确认旧 EventBus/parser/registry 已删除、无双轨分支且未扩大到 TEST_CASE_QA
- [x] 8.5 在隔离环境验证发布流程：停止新 Run、drain/收口 active Run、备份数据库、同步部署 migration/backend/frontend、双 Tab smoke test 后再开放流量
- [x] 8.6 实现完成后更新 `docs/architecture/platform/chat-streaming.md`、`docs/architecture/platform/durable-agent-runs.md`、当前主 specs 与部署文档，只描述已实现 Current 行为
