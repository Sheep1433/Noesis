## 1. 文档（首版）

- [x] 1.1 在 `docs/prd/platform/SSE流式数据设计.md` 增加附录：`task` 工具 input/output 字段与 SubagentCollapse 消费约定（明确不新增 SSE 事件类型）
- [x] 1.2 在 `docs/test/test_tdd_design.md` 补充测试点：`parseTaskToolInput`、`parseTaskToolOutput`（成功/失败/超时/进行中/缺字段）

## 2. 解析工具（首版）

- [x] 2.1 新增 `frontend/src/utils/parseTaskTool.ts`：导出 `TASK_TOOL_NAME` 常量、`parseTaskToolInput`、`parseTaskToolOutput`、`shouldRenderSubagentPart(part)`
- [x] 2.2 （可选）为 `parseTaskTool.ts` 增加 Vitest 单元测试

## 3. SubagentCollapse 组件（首版）

- [x] 3.1 新增 `frontend/src/components/SubagentCollapse/index.vue`：`appearance="light"`，展示 description、subagent_type Tag、状态、prompt、result/error
- [x] 3.2 样式对齐 `ToolCallCollapse` 浅色嵌入模式（复用 token / 间距，避免两套视觉语言）

## 4. chat.vue 接线（首版）

- [x] 4.1 在 assistant `content.parts` 循环中：`toolName === task` → `SubagentCollapse`，其余 tool → `ToolCallCollapse`
- [x] 4.2 确认 `MarkdownPreview/index.vue` 内嵌 tool 渲染路径（若有）对 `task` 做相同分支或委托同一组件
- [x] 4.3 确认流式路径无需改 `useSSEStream` 事件类型解析（parts 已由现有 `onToolCall` / `onToolResult` 维护）

## 5. 验证（首版）

- [x] 5.1 运行 `pnpm lint`（受影响前端文件）
- [x] 5.2 手动冒烟：在暴露 `task` 工具的 Agent（如 FaultOperation/深度研究）发起多步子任务对话，确认 SubagentCollapse 流式更新与刷新后历史恢复
- [x] 5.3 确认未挂载 SubAgent 的 Agent 无 `task`（深度研究已挂载 `research-worker` 子 Agent）

## 6. 二期预留（子会话 drill-down，本 change 不实现）

- [x] 6.1 在 design 或 PRD 中记录：后端创建 `parent_id` 子会话后，可接线 `getSessionChildren` 实现子会话 drill-down

---

## 7. 嵌套打标：parentTaskCallId（已实现）

> **背景**：子 Agent 内部 tool 经 `astream_events` 冒泡，当前平铺在主界面；本节将 tool 收进 SubagentCollapse。

### 7.1 文档

- [x] 7.1.1 更新 `docs/prd/platform/SSE流式数据设计.md` §2.2.2：补充 `parentTaskCallId` 字段、桥接打标规则、前端嵌套渲染与旧数据降级
- [x] 7.1.2 更新 `docs/test/test_tdd_design.md`：桥接打标、buildDisplayParts、嵌套/并行 task、无字段降级

### 7.2 桥接层与落库

- [x] 7.2.1 `langgraph_sse_bridge.py`：维护 run_id 映射与 task 栈；非 task 的 on_tool_* 写入 `parentTaskCallId`
- [x] 7.2.2 `message_builder.py` `ToolPart` 增加 `parent_task_call_id`；序列化键与前端对齐
- [x] 7.2.3 `test_langgraph_sse_bridge_contract.py`：mock parent_ids，断言 SSE JSON 含正确 `parentTaskCallId`

### 7.3 前端嵌套

- [x] 7.3.1 `messageParts.ts`：`ToolUiPart.parentTaskCallId`；`normalizeApiContent` / `upsertToolInputPart` 透传
- [x] 7.3.2 新增 `buildDisplayParts`（或 `groupAssistantParts.ts`）：主循环跳过 child；挂到 task part
- [x] 7.3.3 `SubagentCollapse`：接收 `childParts`，展开区渲染 ToolCallCollapse 列表
- [x] 7.3.4 `chat.vue` 改用 display parts 渲染；`useSSEStream` 回调透传 `parentTaskCallId`（若 SSE 携带）

### 7.4 验证

- [x] 7.4.1 FaultOperation/深度研究冒烟：内部 tool/text 仅出现在 SubagentCollapse 内，主界面不再平铺
- [x] 7.4.2 并行两个 task 时 child 不串台；刷新后嵌套结构恢复（contract 测试覆盖；复杂并行可回归）
- [x] 7.4.3 无 `parentTaskCallId` 的历史消息仍可正常渲染（降级平铺）
