## MODIFIED Requirements

### Requirement: 场景级多知识库 RAG（非测试点级）

对每个进入用例生成的场景，系统 SHALL 使用统一查询文本召回上下文（至少含 `scene_name` 与 `scene_description`；MAY 拼接采纳测试点 `point_name`）。**SHALL NOT** 以 `chunk_indexes` 作为召回依据。

三路通道 SHALL 经 **`KbRetrievalService`** 执行，并读取**目标 collection** 的 MySQL `query_params`，再与通道级覆盖合并（见下表）。未配置时 SHALL 使用 `kb-retrieval` 平台默认（hybrid + rerank 可用时启用）。

| 顺序 | 通道 | trace key | 数据源 | 通道级 query 覆盖 |
|------|------|-----------|--------|-------------------|
| 1 | 当前需求文档 | `current_requirement` | `requirement_docs` | `filters.file_name` ∈ `source_file_names`；`final_top_k=3` |
| 2 | 历史相关需求 | `historical_requirements` | 同 `requirement_docs` | 排除 `source_file_names`；`final_top_k=3`；默认关闭（`CASE_RAG_HISTORICAL_REQUIREMENTS_ENABLED`） |
| 3 | 历史测试用例 | `historical_test_cases` | `test_case_docs_collection` | 无 file 过滤；`final_top_k=3` |

通道级 `final_top_k=3` **SHALL 覆盖**集合 MySQL 默认的 `final_top_k=10`，以与 `test-case-agent-eval` 阶段 B 的 Recall@3 / Hit@3 对齐。`recall_top_k`、`use_reranker`、`search_mode` **SHALL** 仍取自集合配置（缺省 hybrid + rerank）。

#### Scenario: 三路通道使用 hybrid 与 rerank

- **WHEN** 阶段 B 对某场景执行任一路召回，且集合配置 `use_reranker=true`、rerank 可用
- **THEN** 检索 SHALL 经 `KbRetrievalService` 且 `search_mode=hybrid`
- **AND** SHALL 经 rerank 后返回至多 3 条

#### Scenario: 通道 Top-K 覆盖集合默认

- **WHEN** 集合 `query_params.final_top_k=10` 且阶段 B 执行 `current_requirement` 通道
- **THEN** 该通道实际 `final_top_k` SHALL 为 3

#### Scenario: 纯向量不得为默认

- **WHEN** 集合未显式将 `search_mode` 设为 `vector`
- **THEN** 场景 RAG SHALL NOT 默认仅走向量检索

### Requirement: 阶段 B 默认混合检索

对阶段 B 所有语义召回通道，系统 SHALL 默认 `search_mode=hybrid`（BM25 + 向量 + RRF），除非集合 `query_params` 显式设为其它模式。

#### Scenario: 三路通道使用 hybrid

- **WHEN** 阶段 B 对某场景执行任一路召回
- **THEN** 检索请求 SHALL 使用 `search_mode=hybrid`，而非纯向量检索

### Requirement: 召回可观测 trace

系统 SHALL 在 `retrieval_trace` 中以 `scene_name` 为主键记录各 channel 的 `hit_ids`；MAY 含 `recall_score` / `rerank_score` 摘要供调试。供 `test-case-agent-eval` 阶段 B Recall@3 / Hit@3 对账。

#### Scenario: eval 按场景对账

- **WHEN** 离线评测读取 `retrieval_trace`
- **THEN** 每条 trace SHALL 含 `scene_name` 与分 channel 命中列表
