## Context

chat 页停止生成当前实现为 `sseStream.stop()` → `abortController.abort()`，不调用 `POST /api/chat/sessions/{session_id}/stop`。`useSSEStream` 将 `AbortError` 映射为 `settleSuccess()` → `onFinish`，`markStreamingPartsComplete` 把 `running` 工具标为 `success`。

后端已有 `QaService.stop_chat`（落库 + `cancel_task`）与 `exec_query` 的 `CancelledError` 分支（`partial` 落库），以及 `stream_failure_notice`（生成错误收尾）、`DanglingToolCallMiddleware`（checkpointer 悬空 tool call 兜底）。关页 `beforeunload` 已通过 `sendBeacon` 调 `/stop`。

问题：停止语义分裂（客户端 abort vs 服务端 stop）、落库可能双写（`stop_chat` 与 `_finalize_streaming_assistant`）、tool call 中途停止时 UI/DB 与「用户中断」语义不一致。

## Goals / Non-Goals

**Goals:**

- **单一用户停止路径**：仅 `POST /stop`；SSE 保持连接直至服务端 `abort` / `finish` / `[DONE]`。
- **权威落库**：`stop_chat`（或抽出的 `_finalize_stopped_assistant`）唯一写入用户停止后的 assistant 内容；含 flush `text_buffer`、running 工具 → `error`、用户中断说明、`status=partial`、`extra.finish_reason=stopped`。
- **三类终态分流**：用户停止、网络/连接中断、生成失败互不混用前端 `onFinish` / `onError` 与落库文案。
- **SSE 契约兼容**：保留 `abort` 事件；`finish` 扩展 `finish_reason=stopped`；不新增必选业务帧类型。

**Non-Goals:**

- 不引入 Last-Event-ID 断线重放。
- 不改变 `DanglingToolCallMiddleware` 行为（仍兜底 checkpointer）。
- 不改造 `TEST_CASE_QA` 阶段业务状态机（仅共用停止收尾）。
- 不要求 `/stop` 同步返回完整 `content.parts`（仍以 SSE + 历史接口为准）。

## Decisions

### Decision 1: 用户停止仅走后端 `/stop`，前端不 abort SSE

`stopChatStream` / 加载中发送键：**只** `stopChat(sessionId, qaType)`；**删除**停止路径上的 `abortController.abort()`。

SSE `sendMessage` 读循环持续运行，直至收到 `abort` 或 `finish`（非 `error`）及 `[DONE]`，走 `onFinish`。

`beforeunload` 的 `sendBeacon` 与按钮停止同路径，无需改动语义。

**替代方案**：abort + stop 组合——用户明确拒绝，不采用。

### Decision 2: `stop_chat` 为唯一权威落库，流式协程设 `user_stopped` 旗标

在 `QaService` 维护 `session_id → { builder, ctx, user_stopped }`（或等价结构），`stop_chat` 顺序：

1. 设 `user_stopped=True`
2. flush `ctx["text_buffer"]` 入 builder
3. `append_user_stop_notice_to_content(builder.to_dict())`（扩展 `stream_failure_notice`）
4. `_persist_assistant(..., status="partial", extra={..., finish_reason: "stopped"})`
5. `cancel_task`（按 `qa_type`）
6. 清理注册表

`exec_query` / `exec_test_case_resume` 正常结束路径：若 `user_stopped`，**跳过** `_finalize_streaming_assistant` 的 completed 落库，仅 `bridge.finalize()` 发 SSE（`abort` 已由 `__tw_abort__` 发出，`finish` 可带 `finish_reason=stopped`）。

`CancelledError` 路径：若 `user_stopped` 已为真，**不再**二次落库；否则按「连接中断」落库（running 工具标 `error`，**无**用户中断文案）。

**替代方案**：仅在流式协程落库——与并行 HTTP `/stop` 竞态大，不采用。

### Decision 3: 用户停止说明文案与生成错误共用结构、不同 copy

在 `backend/utils/stream_failure_notice.py` 新增：

- `USER_STOP_NOTICE` / `get_user_stop_notice_text(has_prose, parts)`
- `append_user_stop_notice_to_content(content_dict)`：running 工具 `error` 设为「用户已停止生成」；追加 text「（本轮回复已被用户中断。）」或等价中文。

前端 `messageParts.ts` 增加对齐函数 `appendUserStopNotice`，供：

- `onFinish` 且 `finish_reason===stopped` 时本地对齐（可选，刷新后以 DB 为准）
- 历史加载：`extra.finish_reason===stopped` 时补说明（若 DB 已含则跳过）

连接类错误继续 `isConnectionOrTimeoutError` → 气泡内 `null` 说明，仅 Toast。

**替代方案**：前端独自拼文案——与 DB 不一致，不采用。

### Decision 4: SSE `finish_reason=stopped` 与 `abort` 共存

`cancel_task` → `__tw_abort__` → bridge 发 `abort`；`finalize` 发 `finish`，`finish_reason=stopped`（在 `user_stopped` 时由 bridge 或 `exec_query` 注入）。

前端 `useSSEStream`：

- `abort` → 不单独 settle（等待 `finish`），或 `abort` 后仍等 `finish`/`[DONE]`（保持 `streamSettled` 单次）
- `finish` + `finish_reason=stopped` → `settleSuccess` → `onFinish`
- **删除** `AbortError` → `settleSuccess`

**替代方案**：仅 `abort` 无 `finish`——与现有 `finalize` 必发 `finish` 不一致，需改 bridge；优先扩展 `finish_reason`。

### Decision 5: 移除停止专用 `abortController`；卸载/导航用 `/stop` 或文档化

删除 `useSSEStream.stop()` 及 `abortController`（若仅用于停止）。组件 `onUnmounted` 流式中可调 `sendBeacon` 或 `stopChat`（与 `beforeunload` 一致）。

网络失败仍为 `fetch`/`read` 抛错 → `settleFailure` → `onError`，与用户停止无关。

### Decision 6: 网络中断落库不与用户停止混用

客户端意外断连（未调 `/stop`）：`CancelledError` → `partial` 落库，工具 running→`error`（「工具未返回结果」），**不**追加用户中断说明；前端 `onError` +「网络异常」Toast。

生成失败：现有 `error` + `finish(error)` + `append_stream_failure_notice_to_content`。

## Risks / Trade-offs

- [Risk] `/stop` 与 SSE 并行竞态 → Mitigation：`user_stopped` 旗标 + 单次权威落库；测试覆盖 stop 后 `finalize` 不覆盖。
- [Risk] 用户点停止后 SSE 迟迟不结束 → Mitigation：`cancel_task` 尽快 `__tw_abort__`；前端按钮可显示「正在停止…」直至 `onFinish`。
- [Risk] `/stop` 成功但 SSE 已断 → Mitigation：落库在 `stop_chat` 完成；用户刷新可见；前端 `stopChat` catch 仍结束 loading。
- [Risk] `stop_chat` 无活跃 builder（会话不匹配）→ Mitigation：仍 `cancel_task`；返回明确业务消息；不报错 500。
- [Trade-off] 停止后 SSE 多等几帧才关 loading——换取单一语义，可接受。

## Migration Plan

1. 后端：扩展 `stream_failure_notice` + `stop_chat` 收尾；`exec_query` 增加 `user_stopped` 与跳过 completed 落库。
2. 后端测试：`test_stop_chat_finalize`、`test_user_stop_no_double_persist`。
3. 前端：`stopChatStream` 仅调 API；`useSSEStream` 处理 `finish_reason=stopped`；删除 abort 停止路径。
4. 前端：`messageParts` 用户停止文案；历史加载识别 `finish_reason=stopped`。
5. `uv run app.py` + 相关 pytest + `pnpm lint`（chat 影响文件）。

回滚：恢复 `abortController.stop()` 与旧 `stop_chat` 顺序；旗标逻辑可保留无害。

## Open Questions

- `finish` 在 `abort` 之后是否必须带 `finish_reason=stopped`（建议：是，便于前端与历史一致）。
- `TEST_CASE_QA` resume 停止是否与首轮共用同一 `stop_chat`（建议：是，已按 `qa_type` 分支）。
