## 1. 文档

- [x] 1.1 在 `docs/prd/platform/SSE流式数据设计.md` 增加附录或 §：chat 页消费 `tool-input-available` 且 `toolName=write_todos` 时更新 TodoList（明确不新增事件类型）
- [x] 1.2 在 `docs/test/test_tdd_design.md` 补充测试点：`parseWriteTodosInput`、空数组清空、非法项过滤、会话切换清空

## 2. 前端解析与接线

- [x] 2.1 新增 `frontend/src/utils/parseWriteTodosInput.ts`（或等价模块）：校验 `input.todos` 并返回 `Todo[]`
- [x] 2.2 在 `chat.vue` 的 `onToolCall`（或 `useSSEStream` 专用回调）中：`write_todos` → `update_todos(parseWriteTodosInput(input))`
- [x] 2.3 确认新会话、切换会话、`onFinish`/`onError` 时 `businessStore.todos = []`
- [ ] 2.4 （可选）为 `parseWriteTodosInput` 增加 Vitest 单元测试
- [ ] 2.5 （可选）调整 `TodoList/index.vue` 为调用顺序单列表 + 状态圆点

## 3. Agent 引导（可选）

- [x] 3.1 在 `backend/agent/deep_research_agent.py` system prompt 增加多步任务使用 `write_todos` 的简短规则

## 4. 验证

- [x] 4.1 运行 `pnpm lint`（受影响前端文件）
- [ ] 4.2 手动冒烟：深度研究或故障运维流式对话，确认收到 `write_todos` 后 TodoList 更新；刷新后面板为空
- [x] 4.3 后端 prompt 改动；`uv run app.py` 因端口占用未在本机复跑（语法无变更风险）
