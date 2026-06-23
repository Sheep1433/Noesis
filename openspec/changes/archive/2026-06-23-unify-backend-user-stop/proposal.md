## Why

当前 chat 页「停止生成」通过前端 `abortController.abort()` 掐断 SSE，未调用 `POST /api/chat/sessions/{session_id}/stop`；`AbortError` 被当作正常结束，running 工具会被标为 `success`，与落库的 `partial` / `running` 状态不一致。若在 tool call 阶段停止，下一轮 UI 与历史记录可能呈现「有工具调用但无输出」，且与用户主动停止、网络异常、生成失败等场景混在同一套客户端逻辑中，难以阅读与维护。

需要将**用户主动停止**收敛为唯一路径：仅由后端 `/stop` 触发取消与收尾，SSE 自然结束；网络异常与生成错误保持独立通道，语义清晰、落库一致。

## What Changes

- 前端停止按钮与加载中二次点击发送：**仅**调用 `POST /api/chat/sessions/{session_id}/stop`，**移除**停止路径上的 `abortController.abort()` / `sseStream.stop()`。
- SSE 连接保持打开，直至服务端发出 `abort` / `finish`（`finish_reason=stopped`）与 `[DONE]`；前端经 `onFinish` 结束 loading，**不**弹错误 Toast。
- 后端 `QaService.stop_chat` 成为用户停止的**唯一权威落库点**：flush `text_buffer`、running 工具标 `error`、追加「本轮已被用户中断」说明、`status=partial`、`extra.finish_reason=stopped`，再 `cancel_task`。
- 流式协程收到 `__tw_abort__` 后只负责发 SSE，**不得**再以 `completed` 覆盖 `stop_chat` 已写入的内容。
- 网络/连接中断：不调用 `/stop`；前端 `onError` + Toast；后端 `CancelledError` → `partial` 落库（无用户中断文案）；与 `stream_failure_notice` 现有连接类错误策略一致。
- 生成失败：继续走 SSE `error` + `finish(error)` 与 `appendStreamFailureNotice`，与用户停止区分。
- 页面关闭 `beforeunload` 的 `sendBeacon` `/stop` 与用户点击停止对齐为同一路径（已是后端触发，保持不变）。
- **移除** `useSSEStream` 中 `AbortError` → `settleSuccess` 的特殊分支（停止不再产生 `AbortError`）；`abortController` 若仅服务于停止则可删除，组件卸载依赖 `/stop` 或文档化例外。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `platform-chat`：细化「停止生成」需求——客户端停止仅调 `/stop`、服务端收尾与 `finish_reason=stopped`、与网络异常/生成错误的分流；前端 SSE 消费与用户停止 UI 行为。

## Impact

- 前端：`frontend/src/views/chat.vue`（`stopChatStream`、`handleCreateStylized`）、`frontend/src/views/chat/useSSEStream.ts`、`frontend/src/api/chat.ts`、`frontend/src/views/chat/messageParts.ts`（用户停止说明文案，与 `appendStreamFailureNotice` 对齐或扩展）。
- 后端：`backend/services/qa_service.py`（`stop_chat`、`exec_query` / `exec_test_case_resume` 取消与落库分工）、`backend/utils/stream_failure_notice.py`（`user_stop` 收尾）、`backend/api/chat_api.py`（`/stop` 契约不变）。
- Agent：`cancel_task` 行为不变，经 `__tw_abort__` 结束 SSE。
- 测试：后端 `test_stream_failure_persist` / 新增 `test_stop_chat`；前端可选单测 `messageParts` 用户停止文案。
- 兼容：`POST /stop` 路径与 SSE 帧类型无 breaking change；新增 `finish_reason=stopped` 为扩展值，前端须兼容未知 `finish_reason` 时按成功结束处理。
