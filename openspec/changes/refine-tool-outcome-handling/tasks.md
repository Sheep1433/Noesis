## 1. 后端：执行结果解析

- [ ] 1.1 新增 `backend/domain/chat/streaming/tool_outcome.py`：`ToolOutcome`、`parse_tool_outcome`、`format_user_tool_output`、`outcome_to_sse_fields`
- [ ] 1.2 单测 `test_tool_outcome.py`：进程类白名单、S-03～S-09、S-11 非进程不误判

## 1b. 场景目录单测

- [ ] 1b.1 `test_tool_failure.py` / `test_tool_errors.py`：E-01～E-13、A-01～A-05
- [ ] 1b.2 `test_langgraph_sse_bridge_contract.py`：路径 A/B 超时、S-05 用户 output 格式化、T-02、P-01
- [ ] 1b.3 `test_tool_error_handling_middleware.py`：E-14、P-02、P-03

## 2. 后端：沙箱 execute 抛错

- [ ] 2.1 `aio_sandbox.py`：超时→`ToolTimeoutError`；基础设施→`ToolInfrastructureError`
- [ ] 2.2 `test_aio_sandbox_backend.py`

## 3. 后端：SSE 与落库

- [ ] 3.1 `langgraph_sse`：success 写 `outcome`/`exit_code`/`timed_out`；用户 `output` 用 `format_user_tool_output`；subagent 判定留在 bridge
- [ ] 3.2 `message_builder` / ToolPart 支持 outcome 字段（snake_case）
- [ ] 3.3 bridge contract 测试

## 4. 前端

- [ ] 4.1 `useSSEStream` / `applyToolOutput` 透传 `outcome`、`exit_code`、`timed_out`
- [ ] 4.2 `ToolCallCollapse` 按 platform-chat outcome 表渲染
- [ ] 4.3 `chat.vue` / `SubagentCollapse` 传参

## 5. 验证

- [ ] 5.1 `uv run pytest` 相关套件
- [ ] 5.2 `pnpm lint`
