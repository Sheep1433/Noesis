## 1. 分类模块（`domain/chat/streaming/`）

- [x] 1.1 新增 `backend/domain/chat/streaming/tool_failure.py`：`ToolFailureCategory`、`ToolFailure`、`classify_tool_failure()`、`build_error_tool_message()`、`failure_to_sse_error_fields()`、`USER_TOOL_ERROR_MESSAGES` / `DEFAULT_USER_TOOL_ERROR`
- [x] 1.2 精简 `backend/domain/chat/streaming/failure_notice.py`：基础设施/网络 marker 与 `tool_failure` 对齐，避免双份正则；`sanitize_user_facing_error` 签名保持兼容
- [x] 1.3 单测 `backend/tests/test_tool_failure.py`：覆盖分类样本 + 6 条用户短句 + `unknown` 兜底

## 2. Middleware（`agent/middlewares/`）

- [x] 2.1 重构 `tool_error_handling_middleware.py`：import `domain.chat.streaming.tool_failure`；透传 `[tool_error` 前缀；handler 返回 `status=error` 时重分类
- [x] 2.2 更新 `backend/tests/test_tool_error_handling_middleware.py`：分类、双通道、GraphBubbleUp
- [x] 2.3 确认 `agent/factory.py` → `build_noesis_runtime_middleware()` 栈顺序（dangling → tool error → …）

## 3. SSE Bridge 与 Builder（`domain/chat/`）

- [x] 3.1 `domain/chat/streaming/langgraph_sse.py`：`_on_tool_end` / `_on_tool_error` 走 `classify_tool_failure`；SSE 写 `error` + `errorCategory`；`_safe_append_tool_output` 失败 `common.logging` warning
- [x] 3.2 `task` 工具结束路径：子图失败 → `subagent_failure`
- [x] 3.3 扩展 `backend/tests/test_langgraph_sse_bridge_contract.py`（import `domain.chat.streaming.langgraph_sse`）：error 帧 golden
- [x] 3.4 检查 `domain/chat/message_builder.py` 反序列化空 dict `arguments` 边角（`docs/bug/2026-05-12-*` §2）

## 4. 子 Agent 与工具实现（`agent/tools/`）

- [x] 4.1 审查 `task` / deep research subagent 汇总逻辑，内嵌 error 前缀规范
- [x] 4.2 抽样 `chat_attachment_tools.py`、`web_search_tool.py`、MCP 包装：ValidationError/超时可被分类

## 5. 前端（路径未变）

- [x] 5.1 `frontend/src/views/chat/useSSEStream.ts`、`messageParts.ts`：可选解析 `errorCategory`
- [x] 5.2 `frontend/src/components/ToolCallCollapse/index.vue`：展示后端 `error` 短句；`SubagentCollapse` 继承同语义
- [x] 5.3 无 `errorCategory` 的历史消息渲染不变

## 6. 文档与验收

- [x] 6.1 更新 `docs/test/test_tdd_design.md` 工具失败测试点矩阵
- [x] 6.2 更新 `docs/bug/2026-05-12-message-builder-and-sse-bridge-bugs.md` 状态
- [x] 6.3 手动验收：MCP 断连、web_fetch 超时、并行 tool 其一失败
- [x] 6.4 `uv run pytest tests/test_tool_failure.py tests/test_tool_error_handling_middleware.py tests/test_langgraph_sse_bridge_contract.py tests/test_user_facing_error.py -q`；`uv run app.py`；`pnpm lint`（若改 UI）

## 7. 归档准备

- [x] 7.1 `openspec validate unify-tool-error-handling`
- [x] 7.2 勾选 `docs/TODO.md` P0 工具异常处理项；归档合并 spec 至 `openspec/specs/agent-tool-failure-handling/`
