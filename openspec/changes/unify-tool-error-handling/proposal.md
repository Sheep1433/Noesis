## Why

Noesis 的工具失败路径分散在 `ToolErrorHandlingMiddleware`、`LangGraphSseBridge`、`failure_notice`、`AssistantMessageBuilder` 与前端 `ToolCallCollapse` 等多处。初版 `unify-tool-error-handling` 已落地 **ToolFailureCategory**、双通道文案与 SSE `errorCategory`，但分类兜底依赖 **`_classify_from_text()` 对自由文本做正则匹配**。

业界常见做法是：LLM 侧透传完整技术 detail，分类在**抛出边界**通过异常类型、`errno`、入参校验（如 Pydantic）完成。Noesis 当前把同一套正则同时用于用户短句、LLM `retryable` 与日志，**误报已可预期**。

本 change 将分类改为 **类型优先 + 显式 `ToolFailureError` + 异常链剥链 + unknown 兜底**，并补齐三项边界约定：

1. **`failure_notice` 分流**：单 tool 错误走 `classify_tool_failure`；整轮 stream abort 走 `sanitize_stream_error`（模型超时、recursion、`[INTERNAL_ERROR]` 前缀），避免去掉正则后用户看到英文 abort 原文。
2. **`__cause__` 剥链**：`raise RuntimeError(...) from httpx.*` 仍按链上类型映射。
3. **task `subagent_failure`**：以 `langgraph_sse` 子图状态判定为主，不假设 deepagents 输出 `Task failed.` 文本。

不新增 SSE 事件类型；不引入 scope 外能力（per-tool 自定义错误 UI、工具失败后置 hook、独立遥测正则分桶等）。

## What Changes

- 新增 `backend/domain/chat/streaming/tool_errors.py`：`ToolFailureCategory` 枚举 + `ToolFailureError` 及子类。
- **重写** `tool_failure.py`：决策树、剥链、删除 `_classify_from_text` 正则链。
- **分流** `failure_notice.py`：`sanitize_tool_error` / `sanitize_stream_error`；`langgraph_sse` 整轮 `error` 事件改调后者。
- **增强** `langgraph_sse._resolve_tool_failure`：task 子图含 error tool → `subagent_failure`。
- 改造 `web_providers`、MCP invoke 包装：显式 `ToolFailureError` 或 `raise from` 保留 cause。
- 单测：误报反例、剥链、stream/tool sanitize 分流。

## Capabilities

### Modified Capabilities

- `agent-tool-failure-handling`：分类机制、failure_notice 分流、task 汇总落点、异常链剥链。
- `platform-chat`：无协议变更；`error` / `errorCategory` 语义不变。

## Impact

- **后端领域层**：`tool_errors.py`（新）、`tool_failure.py`（重写）、`failure_notice.py`（分流）、`langgraph_sse.py`（task 判定 + stream sanitize 调用点）。
- **Agent**：middleware、`web_providers`、MCP agent 包装。
- **测试**：`test_tool_errors.py`、`test_tool_failure.py`、`test_user_facing_error.py`、middleware / SSE contract。
- **兼容性**：SSE 类型不变；旧 plain error ToolMessage → `unknown`；整轮 infrastructure 文案仍经 stream 路径脱敏。
