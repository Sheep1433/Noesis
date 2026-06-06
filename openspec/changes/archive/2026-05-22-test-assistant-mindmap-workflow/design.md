## Context

- **现状**：上传走 `uploadDocument` → `extracted_markdown` 写入 `requirementList` 并 **`initValue = markdown`** 刷新 Markmap；发起 `TEST_CASE_QA` 流后，勾选区读 SSE 的 `scenes`，脑图仍停留在需求结构。
- **目标心智**：左侧脑图 = **测试设计产物（场景 / 测试点）**；右侧对话 + 勾选 = **流程与操作**；需求正文不占据脑图，仅在解析与 LLM `document_context` 中使用。
- **约束**：不新增第二套并行 Agent；延续 `CaseCoordinator` + `test-case/resume`；向量库仍为 `requirement_docs` / `test_case_docs`（见 `test-case` 相关配置变更）。

## Goals / Non-Goals

**Goals:**

1. 用户在上传后依次看到明确提示：**正在解析文档** → **解析成功** → **正在生成测试场景与测试点**。
2. 生成结束后，**列表展示全部** `scene_name` 与下属 `test_points`（可勾选）；脑图同步展示**全部**场景/测试点树（非需求目录树）。
3. 用户勾选并二次确认后：**脑图更新为仅含已采纳**的场景与测试点；对话区提示 **正在生成测试用例**，调用 `resume`。
4. 阶段条（`phase-*`）与上述 UI 状态一致。

**Non-Goals:**

- 用脑图编辑测试点（只读展示 + 右侧勾选）。
- 在脑图节点上直接展示用例步骤表格（v1 用对话流 `text-delta` 或后续导出）。
- 替换 Markmap 为其它可视化库。

## 前端状态机

| 状态 ID | 用户可见提示（示例） | 脑图 `initValue` | 右侧主交互 |
|--------|----------------------|------------------|------------|
| `idle` | 引导上传 | 默认欢迎/空 | 上传 |
| `parsing_doc` | 正在解析文档… | 不变或占位「解析中」 | 禁用发送 |
| `parse_done` | 文档解析成功 | **仍不展示需求全文**；可短占位「等待生成场景」 | 自动或用户触发进入生成 |
| `gen_scenes` | 正在生成测试场景与测试点… | 占位或 loading 文案 | SSE `scenario-start` / `phase-start` |
| `pick` | 请勾选要采纳的测试点 | **全量**场景/测试点 Markmap | 勾选列表 + 确认按钮 |
| `pick_applied` | 已采纳 N 个测试点，准备生成用例 | **仅已选**场景/测试点 Markmap | 二次确认 → `resume` |
| `gen_cases` | 正在生成测试用例… | 保持采纳后树（可选高亮） | `text-delta` 累积 |
| `done` | 用例已生成 | 保持采纳树；用例详情在对话/导出 | 导出等 |

**与 SSE `phaseId` 映射（建议）**

| phaseId | UI 状态 |
|---------|---------|
| `parse_requirements` | `parsing_doc` → `parse_done`（解析可在上传 API 完成时本地进入，SSE 仅补强） |
| `generate_test_points` | `gen_scenes` |
| `await_user_confirm` | `pick` |
| `parallel_generate_cases` | `gen_cases` → `done` |

## 脑图 Markdown 生成规则

由 `scenes: TcScene[]` 生成 Markmap 源文本（实现可放在 `frontend/src/views/TestAssistant/scenesToMarkmap.ts` 或同目录 util）：

```markdown
# 测试场景与测试点
## {scene_name}
{scene_description 可选一行}
### {point_name} [{point_level}]
```

- **全量展示**：`pick` 阶段传入完整 `tcScenes`。
- **采纳后展示**：按 `selectedPointNames` 过滤，仅保留含至少一个已选测试点的场景。
- **禁止**：在 `pick` 及之后阶段将 `extracted_markdown` 赋给 `initValue`（需求正文经知识库供给 Agent，不在脑图展示）。

## 上传与解析时序

1. 用户选择文件 → `handleTestCaseKbUpload` → `uploadDocument(requirement_docs, file)`（**file_hash 重复则跳过入库，仍继续**）。
2. 进入 `parsing_doc`；API 成功后进入 `parse_done`，**不**刷新脑图为需求树。
3. `requirementList` 记录 `kbFileName` / `kbCollection`，进入 `parse_done`；用户点击「开始生成」或发送补充说明后，再调用 `runTestCaseAfterUpload`（`file_dict: { [fileName]: "__FROM_KB__" }`）。
4. `CaseCoordinator.resolve_document_context` 经 `KbRetrievalService.fetch_full_document_by_file_name` 拉整篇 → 生成场景/测试点；`testpoints-confirm-required` 后 `scenesToMarkmap` 进入 `pick`。

## 用户确认与 resume

1. `submitConfirmedCases`：先 `scenesToMarkmap(filtered)` 更新脑图 → 进入 `pick_applied` / `gen_cases` → `sse.resumeTestCase`。
2. 用例流结束后 `done`；脑图**不**替换为用例 Markdown 全文（与当前 `onFinish` 把 `casesMarkdown` 写入 `initValue` 的行为 **相反**，属 **BREAKING UX**，本变更要求移除该替换）。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 解析很快、提示一闪而过 | `parse_done` 最少展示 300–500ms 或合并为一条「解析完成，开始生成…」 |
| 模型 JSON 为空 | 后端 `_usable_test_point_count` + 前端空态；不进入 `pick` |
| 脑图与列表不同步 | 单一函数 `scenesToMarkmap`，列表与脑图同源 `tcScenes` |

## Migration Plan

1. 先合 OpenSpec 与 PRD 片段，再改 `TestAssistant.vue`（状态 + 脑图数据源）。
2. 回归：上传 → 勾选 → resume → 确认脑图从未出现需求 H2「范围说明」类结构（除非用户未走生成且手动选中需求项——产品应禁止该路径）。

## Open Questions

- 解析成功提示是否必须等 Qdrant 入库完成：当前上传 API 若已同步入库，则 `parse_done` 以 API 成功为准；否则需后端返回 `indexing` 状态（实现阶段再定，规格要求「对用户可见解析成功」即可）。
