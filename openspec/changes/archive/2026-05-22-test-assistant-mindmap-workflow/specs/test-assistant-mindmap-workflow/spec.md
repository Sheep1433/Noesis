## ADDED Requirements

### Requirement: 测试助手页 SHALL 按阶段向用户展示文档与生成进度

在路由 `TestCaseGenerate`（`TestAssistant.vue`）上，当用户上传需求文档（`.docx` / `.md` / `.markdown`）后，系统 SHALL 在界面中依次提供可识别的进度提示，至少包含：**正在解析文档**、**文档解析成功**（或等价成功文案）、**正在生成测试场景与测试点**。上述提示 SHALL 出现在对话区或页面级状态区，且 SHALL NOT 将「解析成功」误解为「场景与测试点已生成完成」。

#### Scenario: 上传后先提示解析

- **WHEN** 用户通过测试助手上传入口提交合法需求文件且请求进行中
- **THEN** 界面 SHALL 显示「正在解析文档」或项目约定的等价文案，且 SHALL 禁止在未完成解析前进入「待勾选测试点」交互态

#### Scenario: 解析成功后提示生成场景与测试点

- **WHEN** 上传/解析接口返回非空 `extracted_markdown`（或项目约定的成功载荷）且即将或已经发起 `TEST_CASE_QA` 流式请求
- **THEN** 界面 SHALL 显示「文档解析成功」类提示，并 SHALL 显示「正在生成测试场景与测试点」类提示，直至收到 `testpoints-confirm-required` 或等价业务完成信号

### Requirement: 测试助手 SHALL 在生成完成后展示全部场景与测试点供勾选

当 `TEST_CASE_QA` 首轮流式流程产出 `scenes_testpoints`（经 SSE `scenes-testpoints-ready` / `testpoints-confirm-required` 的 `scenes` 字段）且其中至少存在一个非空 `test_points[].point_name` 时，系统 SHALL 在测试助手页展示**全部**场景及其下属测试点，供用户多选勾选；展示形式 SHALL 包含可操作的勾选列表（如 checkbox 列表），且 SHALL 与脑图数据源使用同一份 `scenes` 结构。

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

测试助手 SHALL 消费 `TEST_CASE_QA` 与 `test-case/resume` 上的 `phase-start` / `phase-delta` / `phase-end`（若已实现）及 `scenario-start`、`testpoints-confirm-required` 等事件，使顶部或对话区阶段指示与实际上传解析、生成场景点、待确认、并行生成用例顺序一致。

#### Scenario: 待确认阶段对应 await_user_confirm

- **WHEN** 收到 `phase-end` 且 `phaseId` 为 `await_user_confirm` 或收到 `testpoints-confirm-required`
- **THEN** 界面阶段指示 SHALL 处于「待确认」，且 SHALL 与勾选列表同时可见
