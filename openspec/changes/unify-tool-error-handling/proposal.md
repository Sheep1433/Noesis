## Why

Noesis 的工具失败路径分散在 `ToolErrorHandlingMiddleware`、`LangGraphSseBridge`（`domain/chat/streaming/langgraph_sse.py`）、`failure_notice`、`AssistantMessageBuilder` 与前端 `ToolCallCollapse` 等多处，缺少统一的**错误分类**与**处理流程**约定。实际生产中工具失败形态多样——网络不通、连接超时、执行超时、入参校验失败、MCP/沙箱不可用、子 Agent（`task`）内嵌工具失败等——当前实现要么把原始异常字符串直接塞给模型，要么在 SSE/落库层静默丢失，导致用户看到含糊文案、模型重复重试、或并行 tool 结果错位。

`docs/bug/2026-05-12-message-builder-and-sse-bridge-bugs.md` 已记录并行 tool 输出落库风险；`add-agent-runtime-guards` 引入了 dangling repair 与基础 `ToolErrorHandlingMiddleware`，但未覆盖完整 taxonomy 与跨层语义对齐。需要在不新增 SSE 事件类型的前提下，补齐可验收的统一规范。

## What Changes

- 定义 **ToolFailureCategory** 枚举与分类规则（网络不可达、网络/连接超时、执行超时、入参错误、工具不存在、权限拒绝、基础设施不可用、子 Agent 失败、用户取消、未知）。
- 新增 `backend/domain/chat/streaming/tool_failure.py` 作为**单一分类与文案出口**：供 middleware、工具实现、SSE bridge、落库与日志共用。
- 增强 `ToolErrorHandlingMiddleware`：异常与工具返回的 `status=error` 统一走分类 → 结构化 `ToolMessage`（含 `error_category` 元数据或约定 content 前缀），并区分 **LLM 可读详情** 与 **用户可见短句**（6 条固定中文）。
- 规范 `LangGraphSseBridge`（`domain/chat/streaming/langgraph_sse.py`）的 `on_tool_end` / `on_tool_error`：`tool-output-available` 的 `status=error` 必须携带分类后的 `error` 字段；禁止静默吞掉 builder 写入失败（`common.logging` warning + 按 `tool_call_id` 兜底）。
- 子 Agent（`task` 工具）失败：内嵌 tool 错误须向上汇总为可分类的 task 结果，并在 `parentTaskCallId` 子树内保持 error 语义一致。
- 明确各分类的**后续流程**：默认转为 error `ToolMessage` 让主 Agent 继续推理；用户侧仅展示固定短句，细节进模型上下文与日志。
- 补充单测与 golden SSE 契约测试；更新 `docs/test/test_tdd_design.md` 测试点。

## Capabilities

### New Capabilities

- `agent-tool-failure-handling`：工具失败分类、middleware 转换、LLM/用户双通道文案、子 Agent 错误传播约定。

### Modified Capabilities

- `platform-chat`：既有 `tool-output-available`（`status=error`）与 assistant `content.parts` tool part 的 `error` 字段语义扩展（可选 `errorCategory`），不新增 SSE 事件类型。

## Impact

- **后端领域层**（`backend/domain/chat/`）：
  - 新增 `streaming/tool_failure.py`
  - 改造 `streaming/langgraph_sse.py`、`streaming/failure_notice.py`、`message_builder.py`
- **Agent 运行时**（`backend/agent/`）：
  - `middlewares/tool_error_handling_middleware.py`
  - `factory.py`（栈顺序不变）
  - `tools/*`（`chat_attachment_tools.py`、`web_search_tool.py` 等）与 MCP 包装层
- **编排**：`services/qa_service.py`（落库与 stop/disconnect 路径，复用 `failure_notice`）
- **基础设施**：`common/logging.py`（日志，禁止回退 `utils/log_util`）
- **前端**（路径未变）：`ToolCallCollapse` / `SubagentCollapse` 直接展示后端 `error` 短句
- **测试**：`tests/test_tool_failure.py`（新增）、`test_tool_error_handling_middleware.py`、`test_langgraph_sse_bridge_contract.py`、`test_user_facing_error.py`
- **兼容性**：对外 SSE 事件类型不变；新增 JSON 可选字段 `errorCategory`
