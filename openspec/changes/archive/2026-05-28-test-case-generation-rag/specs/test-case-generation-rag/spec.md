## Purpose

本能力规定测试用例流水线**阶段 A**（场景+测试点）与**阶段 B**（按**测试场景**并行、为场景内测试点生成用例）的 RAG 与领域语义。阶段 B 在**场景粒度**做一次多知识库召回并共享上下文，**不**按每个测试点单独 RAG，**不**使用测试点上的 `chunk_indexes`。

## ADDED Requirements

### Requirement: 两阶段流水线边界

系统 SHALL 在阶段 A 输出 `scenes_testpoints`：每个场景含 `scene_name`、`scene_description` 及下属 `test_points`（仅标题级 `point_name` 与元数据）。

系统 SHALL 在阶段 B：（1）仅处理用户采纳的测试点；（2）按**场景**分组；（3）对每个场景执行**一次** RAG 组装；（4）在该场景内**并行**为各采纳测试点生成用例 JSON（`test_steps`、`expected_results` 等）。

#### Scenario: 阶段 B 按场景分组

- **WHEN** 用户采纳的测试点分属场景 S1、S2
- **THEN** 阶段 B SHALL 对 S1、S2 各执行至多一次场景级 RAG（共 2 次），而非按测试点数量执行 RAG

### Requirement: 测试点与测试用例的领域语义

阶段 A 的 `test_points` **SHALL NOT** 包含 `chunk_indexes` 或完整 `test_steps` / `expected_results`。

阶段 B 输出的每个用例 **SHALL** 对应单一 `point_name`，且 **SHALL** 包含 `test_steps` 与 `expected_results` 字段；用例正文为测试点标题的详细展开。

#### Scenario: 阶段 A schema 无 chunk_indexes

- **WHEN** 审查阶段 A 产出 JSON schema 与提示词
- **THEN** SHALL NOT 要求或示例 `test_points[].chunk_indexes`

### Requirement: 用户上传等价于选定设计文档

用户上传并入库的需求文档 SHALL 写入 `requirement_docs`，并作为阶段 A 的 `document_context` 与阶段 B 需求文档召回数据源之一（**不**依赖全库选文档路由）。

#### Scenario: 上传文档进入需求库

- **WHEN** 用户在测试助手上传需求文档并成功入库
- **THEN** 文档分片 SHALL 写入 `requirement_docs` collection，且阶段 A/B SHALL 可直接使用该文档作为上下文与召回源

### Requirement: 场景级多知识库 RAG（非测试点级）

对每个进入用例生成的场景，系统 SHALL 使用统一查询文本召回上下文，查询文本 **SHALL** 至少包含 `scene_name` 与 `scene_description`；**MAY** 拼接本场景已采纳测试点的 `point_name` 作为补充关键词。**SHALL NOT** 以单个测试点的 `chunk_indexes` 作为召回依据。

双路通道：

| 顺序 | 通道 | 数据源 | 检索方式 |
|------|------|--------|----------|
| 1 | 需求文档 | `requirement_docs` | `hybrid`；Top-K 默认 3 |
| 2 | 历史测试用例 | `test_case_docs_collection` | `hybrid`；Top-K 默认 3 |

组装结果 `scene_rag_context` SHALL 在同场景内所有测试点用例生成中**复用**（同 prompt 参考段）。

#### Scenario: 同场景多测试点共享 RAG

- **WHEN** 场景「登录」有 3 个采纳测试点
- **THEN** 系统 SHALL 对「登录」仅调用一次双路召回，且 3 个用例生成 SHALL 使用相同 `scene_rag_context`

#### Scenario: 禁止使用 chunk_indexes

- **WHEN** 阶段 B 组装 RAG
- **THEN** SHALL NOT 调用 `retrieve_chunks(test_point.chunk_indexes)` 作为默认路径

### Requirement: 场景并行与测试点并行

系统 SHALL 对多个场景**并行**调度（受并发上限约束，如全局 Semaphore）；在每个场景任务内，对该场景采纳测试点**并行**调用用例生成 LLM。

#### Scenario: 场景级与点级并发

- **WHEN** 2 个场景各有 4 个采纳测试点
- **THEN** RAG 调用次数 SHALL 为 2；用例 LLM 调用次数 SHALL 为 8

### Requirement: 阶段 B 默认混合检索

对阶段 B 所有语义召回通道（含当前上传文档分片），系统 SHALL 默认 `search_mode=hybrid`（BM25 + 向量 + RRF）。

#### Scenario: 双路通道使用 hybrid

- **WHEN** 阶段 B 对某场景执行 `requirement_docs` 或 `test_case_docs_collection` 召回
- **THEN** 检索请求 SHALL 使用 `search_mode=hybrid`，而非纯向量检索

### Requirement: 召回可观测与评测

`retrieval_trace` **SHALL** 以 `scene_name` 为主键记录各 channel 的 `hit_ids`（供 `test-case-agent-eval` 的 `rag_hit_at_k`）。**SHALL NOT** 要求 per-point trace 作为 RAG 验收依据。

#### Scenario: eval 按场景对账

- **WHEN** 离线评测读取 `retrieval_trace`
- **THEN** 每条 trace 项 SHALL 含 `scene_name` 与分 channel 命中列表
