## 1. 文档

- [x] 1.1 更新 `docs/prd/platform/SSE流式数据设计.md`：`tool-output-available.durationMs`、`usage-update` 事件、`finish.usage` 累计语义与 parts/extra 持久化
- [x] 1.2 在 `docs/test/test_tdd_design.md` 补充测试点：bridge 去重累计、durationMs、前端 usage 行与历史恢复

## 2. 后端 LangGraphSseBridge

- [x] 2.1 在 bridge ctx 增加 `tool_start_times`、`usage_cumulative`、`usage_seen_run_ids`
- [x] 2.2 `_on_tool_start` 记录 `perf_counter()`；`_on_tool_end` / `_on_tool_error` 计算并在 `tool-output-available` 写入 `durationMs`
- [x] 2.3 处理 `on_chat_model_end`（及必要时 stream 末 chunk）提取 `usage_metadata`，按 `run_id` 去重累计
- [x] 2.4 每轮 LLM 结束后 emit `usage-update`；`__tw_finish__` / `finalize` 的 `finish.usage` 填入累计值
- [x] 2.5 扩展 pytest golden：durationMs、usage-update、多轮累计 finish.usage

## 3. 后端持久化

- [x] 3.1 `message_builder` / reduce 路径：tool part 写入 `durationMs`；finish 时 `extra.usage` 与 bridge 累计一致
- [x] 3.2 调整 `base_agent._stream_agent_response`：`__tw_finish__` 不再硬编码占位 usage（由 bridge merge）
- [x] 3.3 `uv run app.py` 验证进程可拉起

## 4. 前端 SSE 与 parts

- [x] 4.1 `useSSEStream`：解析 `usage-update`，回调 `onUsageUpdate`；`tool-output-available` 传递 `durationMs`
- [x] 4.2 `messageParts.ts`：`ToolUiPart` 增加可选 `durationMs`；`applyToolOutput` 写入
- [x] 4.3 `chat.vue`：实现 `onUsageUpdate` 写入当前 assistant 消息 `extra.usage`（或等价 ref）

## 5. 前端 UI

- [x] 5.1 `ToolCallCollapse`（及 `SubagentCollapse` 若适用）header 展示格式化 `durationMs`
- [x] 5.2 assistant 气泡底部增加累计 token 摘要行（input / output / total）；无数据时隐藏
- [x] 5.3 历史消息从 `extra.usage` 与 `parts[].durationMs` 恢复展示

## 6. 验证

- [x] 6.1 运行 `pnpm lint`（受影响前端文件）
- [x] 6.2 手动冒烟：多步 Agent（含至少 2 次工具 + 2 轮 LLM），确认工具耗时与累计 token 流式更新及刷新后历史一致
- [x] 6.3 确认 provider 无 usage 时 finish 正常、UI 不展示 token 行
