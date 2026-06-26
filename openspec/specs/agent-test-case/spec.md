## Purpose

本能力规定 Noesis 第 3 个 Agent 场景 **测试用例生成（`TEST_CASE_QA`）** 的端到端验收标准，覆盖后端流水线、SSE 业务事件、恢复接口与测试助手 UI。

文档按以下章节组织（各章内以 `### Requirement` 表述 SHALL/Scenario）：

1. **流水线** — 两阶段 A/B、`CaseCoordinator`、`LangGraph StateGraph`、场景级 RAG、并行策略
2. **SSE 业务事件** — `phase-*`、`scenes-testpoints-ready`、`testpoints-confirm-required` 等 TEST_CASE_QA 专用帧
3. **恢复接口** — `POST /api/chat/sessions/{session_id}/test-case/resume`
4. **测试助手 UI** — `TestAssistant.vue` 状态机、脑图、勾选

**不在本 spec 重复**：

- 平台级通用 SSE 基础设施（注释保活、半包解析、`LangGraphSseBridge` golden 断言、`[DONE]` 收尾等），见 `platform-chat`
- 离线评测（`evals.case` 两阶段 promptfoo），见 `test-case-agent-eval`

**合并来源**（已归档至本 spec）：原 `test-case-generation-rag`、`test-assistant-mindmap-workflow`，以及原 `chat-sessions-and-streaming` 中 TEST_CASE_QA 相关的 phase SSE、resume、scenes-testpoints-ready 条款。
## Requirements

<!-- 1. 流水线 -->

### Requirement: 两阶段流水线边界

系统 SHALL 在**阶段 A** 输出 `scenes_testpoints`：每个场景含 `scene_name`、`scene_description` 及下属 `test_points`（仅标题级 `point_name` 与元数据）。

系统 SHALL 在**阶段 B**：（1）仅处理用户采纳的测试点；（2）按**场景**分组；（3）对每个场景执行**一次** RAG 组装；（4）在该场景内**并行**为各采纳测试点生成用例 JSON（`test_steps`、`expected_results` 等）。

阶段 A 完成后，图 SHALL 在 `generate_test_cases` 节点前 **interrupt**，等待用户勾选并经 `resume` 进入阶段 B。

#### Scenario: 阶段 B 按场景分组

- **WHEN** 用户采纳的测试点分属场景 S1、S2
- **THEN** 阶段 B SHALL 对 S1、S2 各执行至多一次场景级 RAG（共 2 次），而非按测试点数量执行 RAG

#### Scenario: 阶段 A 完成后中断等待确认

- **WHEN** `generate_scenes_testpoints` 节点成功产出有效 `scenes_testpoints`
- **THEN** `current_phase` SHALL 为 `testpoints_confirm`，且 SHALL NOT 自动进入 `generate_test_cases` 直至 resume

### Requirement: 测试点与测试用例的领域语义

阶段 A 的 `test_points` **SHALL NOT** 包含 `chunk_indexes` 或完整 `test_steps` / `expected_results`。

阶段 B 输出的每个用例 **SHALL** 对应单一 `point_name`，且 **SHALL** 包含 `test_steps` 与 `expected_results` 字段；用例正文为测试点标题的详细展开。

#### Scenario: 阶段 A schema 无 chunk_indexes

- **WHEN** 审查阶段 A 产出 JSON schema 与提示词
- **THEN** SHALL NOT 要求或示例 `test_points[].chunk_indexes`

### Requirement: CaseCoordinator 协调职责

`CaseCoordinator` SHALL 作为 `TEST_CASE_QA` 对外入口，职责包括：

- `run_agent()`：解析 `file_list` 为 `document_context` 与 `source_file_names`，驱动 LangGraph `astream`，在 `testpoints_confirm` 阶段结束首轮流并发出待确认业务帧
- `resume_agent()`：接收 `selected_point_names`，经 `Command(update=…)` 恢复图执行，转发阶段 B 增量事件
- 维护 `_graph_instances`（含 compiled app、config、阶段快照）供 resume 使用；resume 完成后 SHALL 清理实例
- `cancel_task()`：标记取消并清理 graph 实例

#### Scenario: 首轮流在待确认处返回

- **WHEN** 阶段 A 成功且存在可勾选测试点
- **THEN** `run_agent` SHALL 发出 `testpoints-confirm-required` 后结束生成器，且 `_graph_instances` SHALL 保留该会话图实例

### Requirement: LangGraph StateGraph 结构

系统 SHALL 通过 `build_test_case_graph()` 构建 `StateGraph`，节点为 `generate_scenes_testpoints` → `generate_test_cases` → `END`；compile 时 SHALL 设置 `interrupt_before=["generate_test_cases"]`。

`TestCaseState` SHALL 至少包含：`query`、`document_context`、`source_file_names`、`scenes_testpoints`、`selected_point_names`、`test_cases`、`retrieval_trace`、`current_phase`、`error`。

阶段 B 节点 SHALL 为 async，经 `get_stream_writer` 推送 `scene-cases` / `scene-error` custom stream，由 `CaseCoordinator` 映射为 SSE。

#### Scenario: resume 经 Command 注入采纳列表

- **WHEN** 客户端 resume 且会话处于 `testpoints_confirm`
- **THEN** 系统 SHALL 以 `Command(update={"selected_point_names": [...], "current_phase": "test_cases"})` 恢复执行

### Requirement: 用户上传等价于选定设计文档

用户上传并入库的需求文档 SHALL 写入 `requirement_docs`，并作为阶段 A 的 `document_context` 与阶段 B **当前需求**召回的数据源（经 `source_file_names` / `file_name` 过滤）。

`file_dict` 中值为 `__FROM_KB__` 哨兵时，SHALL 以 key 作为 `file_name` 从 `requirement_docs` 拉取整篇正文。

#### Scenario: 上传文档进入需求库

- **WHEN** 用户在测试助手上传需求文档并成功入库
- **THEN** 文档分片 SHALL 写入 `requirement_docs` collection，且阶段 A/B SHALL 可直接使用该文档作为上下文与召回源

### Requirement: 场景级多知识库 RAG（非测试点级）

对每个进入用例生成的场景，系统 SHALL 使用统一查询文本召回上下文，查询文本 **SHALL** 至少包含 `scene_name` 与 `scene_description`；**MAY** 拼接本场景已采纳测试点的 `point_name` 作为补充关键词。**SHALL NOT** 以单个测试点的 `chunk_indexes` 作为召回依据。

三路通道：

| 顺序 | 通道 | trace key | 数据源 | 过滤 / 检索 |
|------|------|-----------|--------|-------------|
| 1 | 当前需求文档 | `current_requirement` | `requirement_docs` | `file_name` 匹配 `source_file_names`；`hybrid` Top-K 默认 3 |
| 2 | 历史相关需求 | `historical_requirements` | 同 `requirement_docs` | 排除 `source_file_names`；默认关闭（`CASE_RAG_HISTORICAL_REQUIREMENTS_ENABLED`） |
| 3 | 历史测试用例 | `historical_test_cases` | `test_case_docs_collection` | 无 file 过滤；`hybrid` Top-K 默认 3 |

组装结果 `scene_rag_context` SHALL 在同场景内所有测试点用例生成中**复用**（同 prompt 参考段）。

#### Scenario: 同场景多测试点共享 RAG

- **WHEN** 场景「登录」有 3 个采纳测试点
- **THEN** 系统 SHALL 对「登录」仅调用一次三路召回，且 3 个用例生成 SHALL 使用相同 `scene_rag_context`

#### Scenario: 禁止使用 chunk_indexes

- **WHEN** 阶段 B 组装 RAG
- **THEN** SHALL NOT 调用 `retrieve_chunks(test_point.chunk_indexes)` 作为默认路径

### Requirement: 场景并行与测试点并行

系统 SHALL 对多个场景**并行**调度（受并发上限约束，如全局 Semaphore，默认 3）；在每个场景任务内，对该场景采纳测试点**并行**调用用例生成 LLM（同场景内一次 LLM 批量展开全部采纳点）。

#### Scenario: 场景级与点级并发

- **WHEN** 2 个场景各有 4 个采纳测试点
- **THEN** RAG 调用次数 SHALL 为 2；用例 LLM 调用次数 SHALL 为 2（每场景 1 次批量调用），而非 8 次独立 RAG

### Requirement: 阶段 B 默认混合检索

对阶段 B 所有语义召回通道，系统 SHALL 默认 `search_mode=hybrid`（BM25 + 向量 + RRF）。

#### Scenario: 三路通道使用 hybrid

- **WHEN** 阶段 B 对某场景执行任一路召回
- **THEN** 检索请求 SHALL 使用 `search_mode=hybrid`，而非纯向量检索

### Requirement: 召回可观测 trace

系统 SHALL 在 `retrieval_trace` 中以 `scene_name` 为主键记录各 channel 的 `hit_ids`（供离线评测阶段 B 的 Recall@K / Hit@K 对账，见 `test-case-agent-eval`）。**SHALL NOT** 要求 per-point trace 作为 RAG 验收依据。

#### Scenario: eval 按场景对账

- **WHEN** 离线评测读取 `retrieval_trace`
- **THEN** 每条 trace 项 SHALL 含 `scene_name` 与分 channel 命中列表

---

<!-- 2. SSE 业务事件 -->

> 本章仅规定 `TEST_CASE_QA` 及 `test-case/resume` 专用业务帧；通用 SSE 帧格式与保活见 `platform-chat`。

### Requirement: TEST_CASE_QA 与 resume 流 SHALL 发出 phase 进度事件

在 `POST /api/chat/sessions/stream` 且 `qa_type` 为 `TEST_CASE_QA`，以及 `POST /api/chat/sessions/{session_id}/test-case/resume` 的流式响应中，系统 SHALL 除既有文本与业务事件外，发出 **`phase-start`、`phase-delta`、`phase-end`** 三类 SSE `data:` JSON 事件。**其它 `qa_type` 的路径 SHALL NOT** 因本要求而必须发送 `phase-*` 事件。

每一段阶段进度 SHALL 使用稳定机器标识 **`phaseId`**（`snake_case`）；系统 SHALL 为至少以下阶段提供展示用 **`title`** 或等价人类可读字段：

| phaseId | 含义 |
|---------|------|
| `parse_requirements` | 解析需求与附件上下文 |
| `generate_test_points` | 生成场景与测试点 |
| `await_user_confirm` | 待用户勾选确认 |
| `parallel_generate_cases` | 并行生成用例 |

同一逻辑阶段 SHALL 以 **`phase-start` → （零次或多次）`phase-delta` → `phase-end`** 的顺序出现；同一 `phaseId` 的一次执行 SHALL 配对一次 `phase-start` 与同 `phaseId` 的 **`phase-end`**。

`phase-start` SHALL 包含至少：`type`、`phaseId`（及可选 `title`）。`phase-end` SHALL 包含至少：`type`、`phaseId`、`ok`（布尔）。`phase-delta` SHALL 包含至少：`type`、`phaseId`，并可包含 `textDelta`。

系统在阶段因用户停止、错误或业务中止而结束时，SHALL 为尚未 **`phase-end` 的当前 `phaseId` 补发 `phase-end`（ok 为假）**，再进入 **`error`** / **`finish`** 语义。

前端对未识别的 SSE `type` 或未知可选键 SHALL **兼容忽略**。

#### Scenario: 首次 TEST_CASE_QA 流出现阶段序列

- **WHEN** 客户端对 `POST /api/chat/sessions/stream` 提交合法载荷且 `qa_type` 为 `TEST_CASE_QA`
- **THEN** 系统 SHALL 依次发出 `parse_requirements`、`generate_test_points`、`await_user_confirm` 相关 `phase-*` 帧，且 JSON 可被标准消费者解析

#### Scenario: resume 后继续 parallel_generate_cases

- **WHEN** 客户端在用户确认测试点后调用 `test-case/resume` 并成功建立流
- **THEN** 系统 SHALL 发出 `parallel_generate_cases` 相关 `phase-*` 事件，并在并行生成收尾时正确闭合 `phase-end`（ok 为真）

#### Scenario: 非测试用例流不强制 phase 事件

- **WHEN** 客户端提交 `qa_type` 不为 `TEST_CASE_QA` 的流式请求
- **THEN** 系统 SHALL NOT 因本条而被要求发射 `phase-start`/`phase-delta`/`phase-end`

### Requirement: 场景测试点就绪与待确认业务帧

系统 SHALL 在 `qa_type=TEST_CASE_QA` 的流式响应中按以下规则发出业务帧：

- 阶段 A 进行中，系统 **MAY** 发出 `scenario-start`（含进度提示文案）
- 阶段 A 完成且进入待确认时，系统 SHALL 发出 **`testpoints-confirm-required`**，其 `data:` JSON **SHALL** 含 `scenes` 数组（结构与 `scenes_testpoints` 一致）及可选 `message`
- 系统 **MAY** 在产出 `scenes` 时额外发出 **`scenes-testpoints-ready`**（载荷与 `testpoints-confirm-required.scenes` 同构）

`scenes` 每项 SHALL 可含 `scene_name`、`scene_description`（可选）及 `test_points` 数组；`test_points` 项 SHALL 含非空 `point_name` 供前端勾选与脑图生成。

客户端（测试助手页）SHALL 将同一 `scenes` 引用同时用于勾选列表与脑图 Markdown 生成，不得依赖额外隐藏字段。

#### Scenario: testpoints-confirm-required 载荷可供双端消费

- **WHEN** 测试助手消费 `testpoints-confirm-required` 事件
- **THEN** `scenes` SHALL 可被解析为至少包含 `scene_name` 与 `test_points[].point_name` 的结构，且 SHALL 无需再请求历史消息即可渲染列表与脑图

#### Scenario: 无可勾选测试点时不发待确认

- **WHEN** 阶段 A 产出零个有效 `point_name`
- **THEN** 系统 SHALL 发出 `error` 而非空的 `testpoints-confirm-required`

### Requirement: 阶段 B 增量 scene-cases 业务帧

在 `test-case/resume` 流中，系统 SHALL 按场景完成进度发出 **`scene-cases`**（成功，含 `sceneName`、`cases`）或等价失败帧（含 `sceneName`、`error`、`pointNames`）；每条场景进度 **SHALL** 伴随同 `phaseId=parallel_generate_cases` 的 `phase-delta` 文案。

#### Scenario: 单场景完成后推送 scene-cases

- **WHEN** 某场景用例批量生成成功
- **THEN** 流中 SHALL 出现 `scene-cases` 帧，且 `cases` 数组中每项 SHALL 含 `point_name`、`test_steps`、`expected_results`

---

<!-- 3. 恢复接口 -->

### Requirement: 测试用例生成恢复端点

系统 SHALL 提供 `POST /api/chat/sessions/{session_id}/test-case/resume`，在 `TEST_CASE_QA` 流程中于用户采纳测试点后继续生成后续内容；响应 SHALL 为 SSE 流，语义与同会话首轮流一致。

请求体 SHALL 含 `selected_point_names`（非空字符串数组）。空数组 SHALL 返回业务失败（HTTP 400 或等价），不得建立流。

#### Scenario: 恢复请求合法

- **WHEN** 客户端在 TEST_CASE_QA 上下文中提交合规 resume 载荷（至少一个 `point_name`）
- **THEN** 系统 SHALL 延续同一逻辑会话（同一 `CaseCoordinator` graph 实例与 checkpoint），继续流式输出阶段 B 内容，且不丢失已生成的 `scenes_testpoints`

#### Scenario: 会话不存在或已过期

- **WHEN** 客户端对无 `_graph_instances` 记录的 `session_id` 调用 resume
- **THEN** 系统 SHALL 在 SSE 中发出 `error`（如「会话不存在或已过期，请重新开始」）及 `finish`（`finishReason=error`）

#### Scenario: 当前阶段不允许 resume

- **WHEN** 会话 graph 实例存在但 `current_phase` 不为 `testpoints_confirm`
- **THEN** 系统 SHALL 拒绝恢复并提示重新发起测试用例生成

---

<!-- 4. 测试助手 UI -->

### Requirement: 测试助手页 SHALL 按阶段向用户展示文档与生成进度

在路由 `TestCaseGenerate`（`TestAssistant.vue`）上，当用户上传需求文档（`.docx` / `.md` / `.markdown`）后，系统 SHALL 在界面中依次提供可识别的进度提示，至少包含：**正在解析文档**、**文档解析成功**（或等价成功文案）、**正在生成测试场景与测试点**。上述提示 SHALL 出现在对话区或页面级状态区，且 SHALL NOT 将「解析成功」误解为「场景与测试点已生成完成」。

#### Scenario: 上传后先提示解析

- **WHEN** 用户通过测试助手上传入口提交合法需求文件且请求进行中
- **THEN** 界面 SHALL 显示「正在解析文档」或项目约定的等价文案，且 SHALL 禁止在未完成解析前进入「待勾选测试点」交互态

#### Scenario: 解析成功后提示生成场景与测试点

- **WHEN** 上传/解析接口返回非空 `extracted_markdown`（或项目约定的成功载荷）且即将或已经发起 `TEST_CASE_QA` 流式请求
- **THEN** 界面 SHALL 显示「文档解析成功」类提示，并 SHALL 显示「正在生成测试场景与测试点」类提示，直至收到 `testpoints-confirm-required` 或等价业务完成信号

### Requirement: 测试助手 SHALL 在生成完成后展示全部场景与测试点供勾选

当 `TEST_CASE_QA` 首轮流产出 `scenes_testpoints`（经 SSE `scenes-testpoints-ready` / `testpoints-confirm-required` 的 `scenes` 字段）且其中至少存在一个非空 `test_points[].point_name` 时，系统 SHALL 在测试助手页展示**全部**场景及其下属测试点，供用户多选勾选；展示形式 SHALL 包含可操作的勾选列表（如 checkbox 列表），且 SHALL 与脑图数据源使用同一份 `scenes` 结构。

#### Scenario: 有可勾选测试点时展示列表

- **WHEN** SSE 载荷中 `scenes` 为数组且合计至少一个 `point_name` 非空
- **THEN** 界面 SHALL 进入「待确认」交互态，并 SHALL 渲染全部可勾选测试点（含场景分组标签）

#### Scenario: 无可勾选测试点时不进入待确认

- **WHEN** `scenes` 为空或无任何有效 `point_name`
- **THEN** 界面 SHALL NOT 仅显示「已生成请在下方勾选」而列表为空；SHALL 提示失败或重试，且 SHALL NOT 将需求文档 Markdown 当作已生成测试点展示

### Requirement: 脑图 SHALL 仅展示测试场景与测试点而非需求原文

测试助手左侧 Markmap（或等价脑图组件）的展示内容 SHALL 由 **`scenes_testpoints` 结构生成的 Markdown 树**驱动，节点层级 SHALL 反映「场景 → 测试点」。在「待确认」及之后阶段，脑图 SHALL NOT 以需求文档 `extracted_markdown` 的全文标题结构（如需求章节「范围说明」「分片规则」等）作为主展示内容。

#### Scenario: 生成完成后脑图为场景测试点树

- **WHEN** 系统进入「待确认」且 `scenes` 非空
- **THEN** 脑图根主题 SHALL 为测试设计语义（如「测试场景与测试点」），子节点 SHALL 包含 `scene_name` 与 `point_name`，且 SHALL NOT 与上传文件章节标题一一对应除非该标题 coincidentally 来自模型输出的 `scene_name`

#### Scenario: 用户采纳后脑图仅含已选测试点

- **WHEN** 用户勾选若干 `point_name` 并确认生成用例
- **THEN** 脑图 SHALL 在发起 `test-case/resume` 之前或同时更新为**仅包含已采纳**测试点及其所属场景的结构化树

### Requirement: 用户采纳测试点后 SHALL 再提示并执行用例生成

用户完成测试点勾选并经二次确认后，系统 SHALL 在界面提示**正在生成测试用例**（或等价文案），并 SHALL 调用 `POST /api/chat/sessions/{session_id}/test-case/resume` 携带已选 `selected_point_names`。用例生成过程的详细文本 SHALL 以流式对话内容为主；**SHALL NOT** 在流式结束后将整段用例 Markdown 覆盖脑图为唯一主视图（脑图保持场景/测试点树）。

#### Scenario: 确认后调用 resume

- **WHEN** 用户确认且至少选择一个测试点
- **THEN** 系统 SHALL 调用 resume 流式接口，并 SHALL 显示用例生成中提示

#### Scenario: 用例完成后脑图仍为主题景测试点

- **WHEN** resume 流式正常结束（`finish` / `[DONE]`）
- **THEN** 脑图 SHALL 仍展示采纳后的场景/测试点树，用例详情 SHALL 通过对话消息或其它非脑图主视图区域呈现

### Requirement: 阶段进度展示 SHALL 与 phase 及业务 SSE 一致

测试助手 SHALL 消费 `TEST_CASE_QA` 与 `test-case/resume` 上的 `phase-start` / `phase-delta` / `phase-end` 及 `scenario-start`、`testpoints-confirm-required` 等事件，使顶部或对话区阶段指示与实际上传解析、生成场景点、待确认、并行生成用例顺序一致。

#### Scenario: 待确认阶段对应 await_user_confirm

- **WHEN** 收到 `phase-end` 且 `phaseId` 为 `await_user_confirm` 或收到 `testpoints-confirm-required`
- **THEN** 界面阶段指示 SHALL 处于「待确认」，且 SHALL 与勾选列表同时可见

---

<!-- 5. 离线评测：见 test-case-agent-eval -->

> 测试用例离线评测（`evals.case` 两阶段 promptfoo、L0 / coverage / RAG 指标）的单一事实来源为 [`openspec/specs/test-case-agent-eval/spec.md`](../test-case-agent-eval/spec.md)。

