# 决策：reliable-sse-multitab 实现收官（spec 57/57）

状态：implemented
日期：2026-08-11
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 可靠 SSE 推送 + 多 tab 共同查看，服务端发现 active run，前端加入已有 run；TEST_CASE_QA 明确不演进（忽略测试用例生成场景）。

**How to apply（关键正确性点）：**
- `RunHandle.apply_event()` 原子边界：projection.apply + sequence + buffer + fan-out 在同一 lock 内。
- `producer_generation` 隔离：旧 producer task 迟到事件被 `StaleProducerGeneration` 拒绝。
- immutable checkpoint snapshot：lock 内捕获，PersistSink 不读 live projection；repository sequence guard 防回退。
- terminal persistence barrier：terminal 事件只 buffer 不 fan-out，DB 写成功后才 fan-out；pending 期间 `pre_terminal_snapshot` 保证 GET/subscribe 返回 N-1。
- stop 幂等：`cancel_requested` + 复用 terminal future。
- `RuntimeEventMapper` 为 raw→typed RunEvent 唯一映射入口，`LcEventMapper` 收敛为别名。
- `GET /sessions/{id}/active-run` 服务端发现 active Run，前端 `resumeActiveRun` 优先走服务端 API、sessionStorage 降级；409 join 冲突响应含完整 schema，前端自动加入。
- SSE subscription 配额（per-run/per-user/global）+ 429；`OWNER_UNAVAILABLE` 503 在开流前拒绝；PG advisory lock 第二个 worker/容器 fail-fast。
- 前端 `useSSEStream` 显式状态机（Discovering → SnapshotReplace → Subscribing → Applying → GapRecovery/Disconnected/Done）+ Playwright 双 Tab E2E 3 场景。
- 429/503 统一走 `ResponseUtil`（code-review 硬性违规项）。

**验证：** 后端 887 passed；前端 lint + build + 14 单元测试；tasks 57/57。spec 真源 `openspec/changes/reliable-sse-multitab/`。
