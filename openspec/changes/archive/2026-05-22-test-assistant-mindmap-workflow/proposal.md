## Why

当前测试助手（`TestAssistant.vue`）将上传后解析得到的**需求 Markdown 全文**直接驱动左侧 Markmap 脑图，而右侧「采纳测试点」依赖模型返回的 **JSON 场景/测试点**；两套数据源未对齐，用户会看到「脑图已是需求章节、勾选区却为空」等困惑。产品期望是：**脑图只展示测试场景与测试点（及用户采纳后的子集）**，需求原文仅在解析与生成阶段作为后台上下文，不作为脑图主视图。

## What Changes

- 定义测试助手页的**分阶段 UI 状态机**：上传 → 解析文档（前台提示）→ 解析成功 → 生成场景与测试点（前台提示）→ **列表展示全部场景/测试点供勾选** → 用户确认后 **将采纳结果写入脑图（Markmap）** → 再提示并进入 **生成对应用例**（`test-case/resume`）。
- **脑图数据源**从「需求 `extracted_markdown`」改为「由 `scenes_testpoints` 结构生成的 Markmap 用 Markdown」；采纳后脑图仅展示**已勾选**的场景与测试点。
- 与既有 **`phase-*` SSE**（`parse_requirements` / `generate_test_points` / `await_user_confirm` / `parallel_generate_cases`）及业务帧（`scenes-testpoints-ready`、`testpoints-confirm-required`）对齐阶段文案，避免步骤条与真实流程脱节。
- **非目标**：脑图展示完整测试用例步骤表（用例生成结果仍以对话区/导出为主，除非后续单独立项）；改造通用 `chat.vue` 测试用例入口（仍跳转测试助手页）。

## Capabilities

### New Capabilities

- `test-assistant-mindmap-workflow`：测试助手页文档解析提示、场景/测试点列表与勾选、脑图内容与阶段切换的单一事实来源。

### Modified Capabilities

- `chat-sessions-and-streaming`：补充测试助手场景下 SSE 阶段与业务帧的**前端消费语义**（阶段文案、脑图刷新时机），不改变其它 `qa_type` 行为。

## Impact

- **前端**：`frontend/src/views/TestAssistant.vue`（状态机、`initValue`/Markmap 生成逻辑、上传与流式回调）。
- **后端**：上传解析 API 已有；`CaseCoordinator` / `case_graph` 产出结构不变，可选增加「解析完成」类 SSE 或沿用上传接口响应 + `phase-*`（实现阶段在 design 中择一）。
- **文档**：实现时需同步 `docs/prd/agent-test-case/测试用例生成设计.md` §7.2 状态机与脑图说明（本变更以 OpenSpec 为准先行）。
