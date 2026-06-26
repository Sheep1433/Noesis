## MODIFIED Requirements

### Requirement: 用户上传等价于选定设计文档

用户上传并入库的需求文档 SHALL 写入 `requirement_docs`，并作为阶段 A 的 `document_context` 与阶段 B **当前需求全文**的数据源（经协调器解析为 `document_context` 字符串注入 prompt）。

`file_dict` 中值为 `__FROM_KB__` 哨兵时，SHALL 以 key 作为 `file_name` 从 `requirement_docs` 拉取整篇正文。

阶段 B **SHALL NOT** 对当前上传文档执行 hybrid Top-K 片段召回作为默认路径；`source_file_names` SHALL 仅用于历史需求通道的 `exclude_file_names` 过滤。

#### Scenario: 上传文档进入需求库

- **WHEN** 用户在测试助手上传需求文档并成功入库
- **THEN** 文档分片 SHALL 写入 `requirement_docs` collection，阶段 A SHALL 使用整篇 `document_context`，阶段 B SHALL 将全文注入用例生成 prompt

### Requirement: 场景级多知识库 RAG（非测试点级）

对每个进入用例生成的场景，系统 SHALL 将 **`document_context` 全文**与 **两路检索片段** 共同组装为用例 prompt 上下文。检索查询文本 **SHALL** 至少包含 `scene_name` 与 `scene_description`；**MAY** 拼接本场景已采纳测试点的 `point_name`。**SHALL NOT** 以 `chunk_indexes` 作为召回依据。

两路检索通道：

| 顺序 | 通道 | trace key | 数据源 | 过滤 / 检索 |
|------|------|-----------|--------|-------------|
| 1 | 历史相关需求 | `historical_requirements` | `requirement_docs` | 排除 `source_file_names`；`hybrid` Top-K 默认 3 |
| 2 | 历史测试用例 | `historical_test_cases` | `test_case_docs_collection` | 无 file 过滤；`hybrid` Top-K 默认 3 |

当前需求 SHALL 以固定 Markdown 小节（如「当前需求文档」）直接拼接 `document_context`，**不**经过向量检索。

组装结果：全文当前需求 + `scene_rag_context`（历史片段）SHALL 在同场景内所有测试点用例生成中**复用**。

#### Scenario: 同场景多测试点共享 RAG

- **WHEN** 场景「登录」有 3 个采纳测试点
- **THEN** 系统 SHALL 对「登录」仅调用一次两路检索，且 3 个用例生成 SHALL 使用相同检索片段与相同 `document_context` 全文

#### Scenario: 禁止使用 chunk_indexes

- **WHEN** 阶段 B 组装 RAG
- **THEN** SHALL NOT 调用 `retrieve_chunks(test_point.chunk_indexes)` 作为默认路径

### Requirement: 阶段 B 默认混合检索

对阶段 B **历史需求与历史用例** 语义召回通道，系统 SHALL 默认 `search_mode=hybrid`（BM25 + 向量 + RRF）。

#### Scenario: 两路通道使用 hybrid

- **WHEN** 阶段 B 对某场景执行 `historical_requirements` 或 `historical_test_cases` 召回
- **THEN** 检索请求 SHALL 使用 `search_mode=hybrid`，而非纯向量检索

### Requirement: 召回可观测 trace

系统 SHALL 在 `retrieval_trace` 中以 `scene_name` 为主键记录 **`historical_requirements`** 与 **`historical_test_cases`** 的 `hit_ids`（供离线评测 Recall@K）。**SHALL NOT** 记录 `current_requirement` 通道。**SHALL NOT** 要求 per-point trace 作为 RAG 验收依据。

#### Scenario: eval 按场景对账

- **WHEN** 离线评测读取 `retrieval_trace`
- **THEN** 每条 trace 项 SHALL 含 `scene_name` 与上述两 channel 的命中列表

### Requirement: 金标准数据集

系统 SHALL 在 `backend/evals/case/datasets/test_case/dataset.jsonl` 维护数据集。每条 item SHALL 含：`id`、`scenario_description`、`document_path`、`ground_truth.golden_test_points`。阶段 B 检索评测 item SHALL 含 `ground_truth.stage_b_scenes[]`（固定 scene fixture + `expected_rag` 仅含 `historical_requirements`、`historical_test_cases`）。

#### Scenario: 数据集规模不强制扩展

- **WHEN** 审查离线评测数据集
- **THEN** SHALL 以仓库现有条目为准验收，**SHALL NOT** 将「至少 20 条」作为通过条件

### Requirement: 离线 runner

系统 SHALL 通过 `uv run python -m evals.case` 启动离线评测，并支持 `--phase stage-a|stage-b`（同一 `promptfoo/` 目录下不同 yaml）。`stage-a` SHALL 仅评阶段 A；`stage-b` SHALL 检索隔离评测。

#### Scenario: stage-a 仅评阶段 A

- **WHEN** 以 `--phase stage-a` 执行
- **THEN** SHALL 使用 `promptfooconfig.stage-a.yaml`，**SHALL NOT** 依赖 Qdrant

#### Scenario: stage-b 检索隔离

- **WHEN** 以 `--phase stage-b` 执行
- **THEN** SHALL 直调 `build_scene_rag_context` 与 fixture Qdrant，**SHALL NOT** 调用用例生成 LLM

### Requirement: coverage（测试点覆盖准确率）

系统 SHALL 用 LLM Judge（promptfoo llm-rubric）计算 `point_coverage_recall` = 已覆盖金标准数 / |golden_test_points|。pytest SHALL 使用 mock Judge，默认 CI 不调 DashScope。

#### Scenario: CI 默认 mock Judge

- **WHEN** pytest 运行 coverage 相关用例且未配置 live Judge
- **THEN** SHALL 使用 mock，**SHALL NOT** 调用 DashScope API

### Requirement: 报告

promptfoo 评测结果 SHALL 支持 baseline 对比。阶段 A 汇总 SHALL 含 `l0_pass_rate`、`point_coverage_recall_mean`、`scene_name_recall_mean`。阶段 B 汇总 SHALL 含两路 `recall_at_3_mean`、`hit_at_3_mean`、`mrr_at_3_mean` 及 `macro_recall_at_3_mean`（有标注项时）。

#### Scenario: stage-b 聚合检索指标

- **WHEN** `--phase stage-b` 完成且存在 `stage_b_scenes` 金标准
- **THEN** 报告 SHALL 分别汇总 `historical_requirements` 与 `historical_test_cases` 的 Recall@3 均值

### Requirement: CI 登记

系统 SHALL 在 `docs/test/test_tdd_design.md` 登记阶段 A（L0、coverage、scene_name_recall）与阶段 B（Recall@3/Hit@3/MRR@3）指标；默认 pytest 不调 DashScope；阶段 B live 测以 `NOESIS_CASE_STAGE_B_EVAL=1` 门控。

#### Scenario: 文档与用例对齐

- **WHEN** 审查测试设计文档
- **THEN** SHALL 列出两阶段评测点、promptfoo 配置路径及 mock 策略

## REMOVED Requirements

### Requirement: rag（RAG Hit@3）

**Reason**: 指标名实不符（非 Recall@K），且含已废弃的 `current_requirement` 通道；由 `test-case-agent-eval` 中阶段 B Recall@3/Hit@3/MRR@3 取代。

**Migration**: 使用 `uv run python -m evals.case --phase stage-b`；金标准迁至 `ground_truth.stage_b_scenes[].expected_rag`。

### Requirement: 离线 runner（--scope testpoints|cases|full）

**Reason**: 由 promptfoo `--phase` 取代直调 scope 参数。

**Migration**: `testpoints` → `--phase stage-a`；检索评测 → `--phase stage-b`。

### Requirement: 报告（旧 aggregate 三指标）

**Reason**: 阶段拆分后报告字段按 phase 分别聚合。

**Migration**: 见 MODIFIED「报告」需求。
