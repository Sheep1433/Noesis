## 1. 异常类型模块

- [x] 1.1 新增 `backend/domain/chat/streaming/tool_errors.py`：`ToolFailureCategory` 枚举 + `ToolFailureError` 基类及子类
- [x] 1.2 单测 `backend/tests/test_tool_errors.py`：构造、category/retryable、子类默认值

## 2. 分类模块重写（`tool_failure.py`）

- [x] 2.1 实现 `format_tool_error_detail(exc, raw)`（截断 10k、拼类型名）
- [x] 2.2 实现 `_iter_exception_chain(exc, max_depth=2)` 与 `_map_exception(node)`
- [x] 2.3 重写 `classify_tool_failure`：决策树（§2）；**删除** `_classify_from_text` 及全部 `_*_MARKERS` / `is_infrastructure_failure`
- [x] 2.4 保留 `build_error_tool_message`、`failure_to_sse_error_fields`、`classify_task_tool_output`、用户文案常量
- [x] 2.5 重写 `backend/tests/test_tool_failure.py`：类型映射、剥链、`__cause__`、误报反例

## 3. Middleware

- [x] 3.1 更新 `tool_error_handling_middleware.py`：日志增加 `tool_failure_detail`；`[tool_error` 透传不变
- [x] 3.2 重写 `backend/tests/test_tool_error_handling_middleware.py`：`ToolInfrastructureError`、`httpx`+cause、`unknown` 不再误判 connection refused 文本

## 4. 工具 / MCP 抛点

- [x] 4.1 `agent/tools/web_providers/local_fetch.py`、`tavily.py`、`ddg.py`：`ToolFailureError` 子类或 `raise from httpx.*`
- [x] 4.2 MCP：`get_tools()` invoke 装饰器（`fault_operation_agent.py`、`simple_mcp_agent.py` 或共享模块）

## 5. failure_notice 分流

- [x] 5.1 新增 `sanitize_tool_error` / `sanitize_stream_error`；`[INTERNAL_ERROR]` 前缀检测仅用于 stream 路径
- [x] 5.2 `langgraph_sse.py`：整轮 `__tw_error__`/`error` 改调 `sanitize_stream_error`；tool 路径仍用 `classify_tool_failure`
- [x] 5.3 更新 `backend/tests/test_user_facing_error.py`：stream infrastructure 脱敏 + tool 误报反例

## 6. Bridge task 子 Agent 判定

- [x] 6.1 `langgraph_sse._resolve_tool_failure`：结合子图 error tool / `task_tool_call_stack` 标 `subagent_failure`
- [x] 6.2 更新 `test_langgraph_sse_bridge_contract.py`：子图 error 判定（不仅依赖 `Task failed.` 字符串）

## 7. 验收

- [x] 7.1 `uv run pytest tests/test_tool_errors.py tests/test_tool_failure.py tests/test_tool_error_handling_middleware.py tests/test_langgraph_sse_bridge_contract.py tests/test_user_facing_error.py -q`
- [x] 7.2 `uv run app.py` 冒烟
- [x] 7.3 `openspec validate unify-tool-error-handling`
