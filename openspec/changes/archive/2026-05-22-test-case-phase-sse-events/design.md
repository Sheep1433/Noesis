## Context

- 对照阅读：`docs/readings/compare/aix-db-vs-noesis-backend-analysis.md` 建议 Noesis 在统一 SSE 协议上增加「业务阶段进度事件」；`docs/readings/compare/deerflow_vs_noesis_backend_analysis.md` 强调 SSE 运行时事件序列完整性。
- **现状**：`CaseCoordinator` 已产出若干测试用例专用帧（如 `scenario-start`、`scenes-testpoints-ready`、`testpoints-confirm-required`、`case-start`、`case-end`），与通用 `LangGraphSseBridge` 事件并列；前端若要做「解析需求 → 生成测试点 → 待确认 → 并行生成」的**统一阶段模型**，需在协议层引入与 Aix-DB 对齐的 **`phase-*` 三件套**，避免各场景自定义零散 type 名称。
- **约束**：仅 `TEST_CASE_QA` 与 `test-case/resume`；其它 `qa_type` 不改变；不引入第二套并行协议。

## Goals / Non-Goals

**Goals:**

- 在同一条 SSE 连接上，对上述端点透出 **`phase-start`、`phase-delta`、`phase-end`**，携带稳定 **`phaseId`** 与可读 **`title`**（或等价展示字段）。
- `phase-start` 与 **`phase-end` 配对**：同一 `phaseId` 在单次用户回合内至多一对 active；结束时可带 **`ok`/状态**区分正常完成与用户中断。
- **`phase-delta` 可选**：用于阶段内短文案或进度摘要（类比 `text-delta` 的子串增量，但语义隶属阶段，不要求与 token 对齐）。
- 后端实现与 **`AssistantMessageBuilder` / multipart 落库**：阶段事件 MAY 进入 `content.parts` 中可追溯片段（与设计取舍一致，规格中写明 SHALL 边界）；若首版不落库，须在规格中写明「仅流经 SSE」。

**Non-Goals:**

- 故障运维、深度研究等其它类型的 `phase-*`。
- Redis Run 回放、`Last-Event-ID` 级别的断线恢复（可参考 DeerFlow，但不在本变更范围）。
- 单独 `phase-error` 帧（阶段失败继续统一走既有 `error`/`abort`，在 `phase-end` 中通过 `ok: false` 收敛可选）。

## Decisions

1. **阶段枚举（`phaseId`）**：采用稳定 **`snake_case` 英文标识**，与 UI 文案解耦；推荐集合（实现可先做子集，但须在代码与规格中同源）：
   - `parse_requirements` — 解析/合并需求与附件上下文；
   - `generate_test_points` — 生成场景与测试点；
   - `await_user_confirm` — 等待用户勾选与确认；
   - `parallel_generate_cases` — 按已选测试点并行生成用例。
   - **`title`/中文标签**由后端或常量表提供，避免把中文写进协议主键。
2. **与现有自定义事件的关系**：保留现有 `scenario-start` 等业务帧以保证兼容；新增 **`phase-*` 为核心进度条数据源**——在 `case_coordinator` 内在关键节点emit `phase-*`（可先对现有节点做一对一映射），不在本变更删除旧类型。
3. **穿出路径**：优先在 **`CaseCoordinator` 异步生成 dict** 与同一路 **`QaService` → SSE 编码**链路发出，样式与既有 dict 一致；若需经 `LangGraphSseBridge`，则仅在桥接识别 `type in {"phase-start","phase-delta","phase-end"}` 时原文转发为 SSE JSON，避免与 LangChain 原生事件耦合过深。
4. **resume 语义**：用户调用 `POST /api/chat/sessions/{session_id}/test-case/resume` 后，从 **`parallel_generate_cases` 的 `phase-start`**（或上一轮 `await_user_confirm` 的 **`phase-end` → 下一阶段 `phase-start`）重新开始阶段序列**，保证 UI 重入一致。
5. **前端**：`useSSEStream` 增加可选分支解析 `phase-*`，未识别 type 的旧逻辑保持不变（忽略未知）；测试用例页可用现有导航/步骤条挂载。

### 备选方案（未采纳）

- **仅用现有业务事件拼装阶段**：省去 `phase-*`，但无法与 Aix-DB / 统一的「阶段契约」对齐，跨 Agent 扩展时仍会碎片化。
- **用 `reasoning-*` 冒充阶段**：污染推理语义，前端难以区分模型思考与业务阶段。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 事件翻倍（旧 + phase）造成噪音 | UI 仅以 `phase-*` 绘制进度条；旧事件保留给已实现功能 |
| `phase-delta` 过多 | 节流或仅在长阶段发送；规格允许「零 delta」 |
| 落库体积增大 | 首版可选用「不写 parts，仅 SSE」或与 `finish` 合并摘要 |

## Migration Plan

1. 先合规格与桥接测试（golden），再改 `CaseCoordinator` / 服务层发事件，最后按需改前端步骤展示。
2. 回滚：停止发送 `phase-*` 帧即可；未读新事件的前端无影响。

## Open Questions

- `phase-*` 是否写入 assistant **multipart**：若产品需要聊天记录中回看阶段，再在后续小变更中将 parts 快照策略从 MAY 升格为 SHALL。
