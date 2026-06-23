## MODIFIED Requirements

### Requirement: 停止生成

系统 SHALL 提供 `POST /api/chat/sessions/{session_id}/stop`，作为**用户主动停止**的唯一业务入口：取消进行中的流式任务、将已生成的 assistant 内容以 multipart 格式权威落库，并通过既有 SSE 连接发出 `abort` 与 `finish`（`finish_reason=stopped`）及 `data: [DONE]` 收尾。

客户端在用户点击「停止生成」或加载中二次触发发送（停止）时 **SHALL ONLY** 调用上述 stop 接口（及页面关闭时等价的 `sendBeacon`），**SHALL NOT** 通过 `AbortController` 或等价手段主动掐断 `POST /api/chat/sessions/stream` 的 fetch 响应体以完成停止。

`QaService.stop_chat` SHALL 为停止后的 assistant 消息**唯一权威落库点**，执行顺序 SHALL 包含：flush 流式 `text_buffer`、将 `status=running` 的 tool part 标为 `error` 并写入用户停止相关错误说明、追加用户可读的「本轮已被用户中断」类 text 说明（与 `stream_failure_notice` 结构对齐）、`status=partial`、`extra.finish_reason=stopped`，随后按 `qa_type` 调用对应 Agent/Coordinator 的 `cancel_task`。

当流式协程因 `cancel_task` 收到 `__tw_abort__` 时，SHALL 继续向客户端发送 SSE 直至 `finalize`；若 `user_stopped` 已为真，SHALL **NOT** 再以 `status=completed` 覆盖 `stop_chat` 已写入的 assistant 内容。

#### Scenario: 用户点击停止仅调 stop 且不 abort SSE

- **WHEN** 用户在 chat 页流式生成过程中点击停止按钮
- **THEN** 前端 SHALL 调用 `POST /api/chat/sessions/{session_id}/stop` 且 **SHALL NOT** 对该轮流式 fetch 调用 `abort()`
- **AND** SSE 读循环 SHALL 保持直至收到 `abort` 或 `finish` 及 `data: [DONE]`
- **AND** 前端 SHALL 经 `onFinish` 结束 loading，且 **SHALL NOT** 弹出错误 Toast

#### Scenario: stop 后服务端收敛并落库

- **WHEN** 客户端在流未完成时调用 stop 且存在活跃流式 builder
- **THEN** 服务端 SHALL 调用 `cancel_task` 中止上游生成
- **AND** SHALL 将 assistant 消息以 `status=partial` 落库，且 `extra.finish_reason` SHALL 为 `stopped`
- **AND** 落库内容中 running 的 tool part SHALL 为 `error` 状态且含用户停止语义的错误说明
- **AND** 落库内容 SHALL 含用户可读的「本轮已被用户中断」类说明 text part（无正文时 MAY 仅含该说明）

#### Scenario: stop 后 SSE 正常结束

- **WHEN** `cancel_task` 使上游产出 `__tw_abort__`
- **THEN** SSE SHALL 发出 `abort` 事件
- **AND** 随后 SHALL 发出 `finish`，其 `finish_reason` SHALL 为 `stopped`（或在与 `abort` 组合时仍保证客户端能识别为成功结束）
- **AND** SHALL 以 `data: [DONE]` 收尾

#### Scenario: 页面关闭与用户点击停止同路径

- **WHEN** 用户在流式生成过程中关闭或刷新页面且 `beforeunload` 触发
- **THEN** 客户端 SHALL 通过 `sendBeacon` 或等价方式调用同一 stop 接口
- **AND** 语义 SHALL 与用户点击停止一致（非网络异常通道）

## ADDED Requirements

### Requirement: 用户停止、网络中断与生成失败 SHALL 分流

系统 SHALL 将流式对话的异常终态分为三条互不复用的路径：**用户主动停止**（`/stop`）、**网络或连接中断**（未调用 `/stop` 的断连）、**生成过程失败**（Agent/桥接层错误）。各路径在前端回调、Toast、assistant 落库文案与 `extra.finish_reason` 上 SHALL 保持可区分。

用户主动停止 SHALL 使用 `finish_reason=stopped` 与用户中断说明；SHALL NOT 使用 `onError` 或连接类错误 Toast。

网络或连接中断 SHALL NOT 调用 `/stop`；服务端 `CancelledError` 落库 SHALL 为 `status=partial` 且 **SHALL NOT** 写入用户中断说明；前端 SHALL 经 `onError` 展示「网络异常，请稍后重试」类 Toast，且对连接类错误 **SHALL NOT** 在气泡内追加长段失败说明（与现有 `isConnectionOrTimeoutError` 策略一致）。

生成过程失败 SHALL 继续发出 SSE `error` 与 `finish`（`finish_reason=error`）；落库与展示 SHALL 使用 `append_stream_failure_notice` / `appendStreamFailureNotice` 语义，且 **SHALL NOT** 使用 `finish_reason=stopped`。

#### Scenario: 网络断开不走用户停止文案

- **WHEN** 流式过程中发生网络断开或 `fetch`/`read` 失败，且客户端未调用 `/stop`
- **THEN** 前端 SHALL 调用 `onError` 并展示网络类 Toast
- **AND** 服务端若因 `CancelledError` 落库，assistant `extra.finish_reason` SHALL NOT 为 `stopped`
- **AND** 落库内容 SHALL NOT 含「本轮已被用户中断」类说明

#### Scenario: 生成失败不走用户停止

- **WHEN** Agent 或桥接层发出 `error` 且 `finish_reason=error`
- **THEN** 前端 SHALL 调用 `onError` 并追加生成失败说明（非用户中断文案）
- **AND** `extra.finish_reason` SHALL 为 `error` 或等价错误标记，SHALL NOT 为 `stopped`

#### Scenario: tool call 阶段用户停止后历史可辨识

- **WHEN** 用户在 assistant 已发出 tool 调用但尚无 tool 输出时调用 `/stop`
- **THEN** 落库的 tool part SHALL 为 `error` 且 SHALL NOT 保持 `running` 或误标为 `success`
- **AND** 助手消息 SHALL 含用户中断说明，使用户刷新会话后不会看到「工具成功但无输出」的歧义状态

### Requirement: chat 页停止 UI SHALL 等待服务端 SSE 结束

在采用后端单一停止路径后，`chat.vue` 的 `stopChatStream`（或等价函数）SHALL 调用 `stopChat` API 后保持 `stylizingLoading`（或等价加载态）直至 `useSSEStream` 的 `onFinish` 触发；SHALL NOT 在调用 stop 后立即本地将 running 工具标为 `success`。

收到 `finish` 且 `finish_reason=stopped` 时，前端 MAY 调用与后端文案对齐的 `appendUserStopNotice` 以同步当前气泡；历史消息加载时若 `extra.finish_reason=stopped` 且 parts 尚无中断说明，SHALL 补全展示。

#### Scenario: 停止后 loading 直至 SSE 收尾

- **WHEN** 用户点击停止且 `/stop` 请求已发出
- **THEN** 输入区加载态 SHALL 保持直至 SSE `onFinish`
- **AND** SHALL NOT 依赖 `AbortError` 结束加载

#### Scenario: 历史消息恢复用户停止状态

- **WHEN** 客户端加载 `status=partial` 且 `extra.finish_reason=stopped` 的 assistant 消息
- **THEN** chat 页 SHALL 展示用户中断说明与 error 态的未完成工具
- **AND** SHALL NOT 将未完成工具展示为成功
