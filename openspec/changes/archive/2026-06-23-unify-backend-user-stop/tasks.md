## 1. 后端：用户停止收尾与权威落库

- [x] 1.1 在 `backend/utils/stream_failure_notice.py` 新增 `append_user_stop_notice_to_content` 与文案常量（running 工具 → `error`、「用户已停止生成」、text「本轮已被用户中断」）
- [x] 1.2 在 `QaService` 扩展流式注册表：`session_id → { builder, ctx, user_stopped }`（或等价），供 `stop_chat` 访问 `text_buffer`
- [x] 1.3 重写 `stop_chat`：设 `user_stopped` → flush buffer → `append_user_stop_notice_to_content` → `_persist_assistant(partial, finish_reason=stopped)` → `cancel_task` → 清理注册表
- [x] 1.4 `exec_query` / `exec_test_case_resume`：正常结束时若 `user_stopped` 则跳过 `_finalize_streaming_assistant` 的 completed 落库；`CancelledError` 时若已 `user_stopped` 则跳过二次落库
- [x] 1.5 `LangGraphSseBridge` 或 `exec_query`：在 `user_stopped` 路径使 `finish` 携带 `finish_reason=stopped`
- [x] 1.6 新增 `backend/tests/test_stop_chat_finalize.py`：tool running 中途 stop、无双重落库、`finish_reason=stopped`、中断说明 parts 断言

## 2. 后端：网络中断与生成失败回归

- [x] 2.1 确认 `CancelledError` 非 `user_stopped` 路径：running 工具标 `error`，无用户中断文案
- [x] 2.2 补充或更新 `test_stream_failure_persist.py`：连接类错误与用户停止文案互斥
- [x] 2.3 运行 `uv run pytest backend/tests/test_stop_chat_finalize.py backend/tests/test_stream_failure_persist.py` 及相关用例

## 3. 前端：单一停止路径

- [x] 3.1 `chat.vue`：`stopChatStream` 改为 `await stopChat(sessionId, qaType)`（fire-and-forget 需 catch），移除 `sseStream.stop()`
- [x] 3.2 `useSSEStream.ts`：删除 `stop()` 与停止用 `abortController.abort()`；删除 `AbortError` → `settleSuccess`；处理 `finish` + `finish_reason=stopped` → `onFinish`
- [x] 3.3 `messageParts.ts`：新增 `appendUserStopNotice`，与后端 copy 对齐；`onFinish` 在 stopped 时调用（或仅依赖历史加载）
- [x] 3.4 `initChatHistory.ts`：`finish_reason=stopped` 时补全中断说明展示
- [x] 3.5 确认 `beforeunload` `sendBeacon` 与按钮停止语义一致，无需双路径 abort

## 4. 验证

- [x] 4.1 手动：tool call 阶段点停止 → 刷新 → 工具为 error、有中断说明、无成功假象
- [x] 4.2 手动：断网模拟 → Toast 网络异常、无「用户中断」文案
- [x] 4.3 `uv run app.py` 验证后端启动；`pnpm lint` 覆盖 `chat.vue`、`useSSEStream.ts`、`messageParts.ts`
