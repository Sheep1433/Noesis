## Why

> **状态（2026-06-26）**：`agent-tool-failure-handling` 与 `platform-chat` **主规格已合并** outcome 双层模型与 SSE 字段约定；**后端 `tool_outcome.py`、沙箱抛错与前端 ToolCallCollapse 尚未实现**。本 change 保留为实现任务清单，归档前须完成 tasks 或回滚主 spec 中未实现条款。

现有 `agent-tool-failure-handling` 规格将「工具调用失败」与「工具已返回但执行结果异常」混为一谈：仅覆盖 `ToolMessage.status=error` 与 middleware 异常路径，无法表达 **execute 成功返回但命令非零退出**、**执行超时却以 success 返回**、**输出为空无法判断原因** 等常见场景。前端 `ToolCallCollapse` 在 `result` 为空时完全不渲染输出区，用户看不到「无输出」或超时/退出码提示。

需要将工具结果拆为 **调用层（invoke）** 与 **执行层（outcome）** 两轴语义，并在 SSE / 落库 / UI 贯通。

## What Changes

- **重写** `agent-tool-failure-handling` 主规格：保留调用失败 `failureCategory` 分类与双通道文案；新增 `ToolOutcome` 执行结果枚举与结构化解析约定。
- `tool-output-available` 在 `status=success` 时 **MAY** 携带 `outcome`、`exitCode`、`timedOut`、`truncated` 可选字段；落库 tool part 对齐。
- 新增 `domain/chat/streaming/tool_outcome.py`：从工具输出（含 execute JSON 形态）解析 outcome，供 `LangGraphSseBridge` 与 `AssistantMessageBuilder` 共用。
- `AioSandboxBackend.execute`：超时与基础设施错误 **SHALL** 抛 `ToolTimeoutError` / `ToolInfrastructureError`（调用失败），**SHALL NOT** 吞异常后返回空 success 输出；正常返回 **SHALL** 输出含 `exit_code` / `timed_out` 的结构化 JSON 字符串。
- 前端 `ToolCallCollapse`：`status=success` 且 outcome 为 `empty` 时展示「（无输出）」；`command_failed` / `timed_out` 展示对应状态标签与退出码/超时提示。
- **不新增** SSE 事件类型；旧客户端可忽略未知 `outcome` 字段。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-tool-failure-handling`：全文重写，引入 invoke/outcome 双层模型与进程类工具结构化约定。
- `platform-chat`：扩展 `tool-output-available` 与 tool part 落库字段；扩展 `ToolCallCollapse` 展示规则。
- `agent-sandbox`：明确 `execute` 超时/失败抛错与结构化成功返回约定。

## Impact

| 区域 | 路径 |
|------|------|
| 规格 | `openspec/specs/agent-tool-failure-handling/spec.md`（替换） |
| 执行结果解析 | `backend/domain/chat/streaming/tool_outcome.py`（新增） |
| 调用失败（保留） | `tool_errors.py`、`tool_failure.py`、`tool_error_handling_middleware.py` |
| SSE 桥接 | `backend/domain/chat/streaming/langgraph_sse.py` |
| 消息构建 | `backend/domain/chat/message_builder.py` |
| 沙箱后端 | `backend/agent/backends/aio_sandbox.py` |
| 前端 SSE | `frontend/src/views/chat/useSSEStream.ts`、`messageParts.ts` |
| 前端 UI | `frontend/src/components/ToolCallCollapse/index.vue` |
| 测试 | `backend/tests/test_tool_outcome*.py`、`test_langgraph_sse_bridge_contract.py`、前端相关单测 |
