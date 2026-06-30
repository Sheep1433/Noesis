## MODIFIED Requirements

### Requirement: 场景级双路召回默认 hybrid 与 rerank

对阶段 B 所有语义召回通道，系统 SHALL 默认 `search_mode=hybrid`（BM25 + 向量 + RRF），并 SHALL 读取目标集合 MySQL `query_params`（含 `use_reranker`、`recall_top_k`、`final_top_k`）。未配置时 SHALL 与平台默认一致（hybrid + rerank 可用时启用）。

#### Scenario: 三路通道使用 hybrid 与集合 query_params

- **WHEN** 阶段 B 对 `requirement_docs` 执行场景级召回
- **THEN** 检索 SHALL 经 `KbRetrievalService` 且 `search_mode` 为 `hybrid`
- **AND** SHALL 应用集合 `query_params` 中的 rerank 与 top-k 设置

#### Scenario: 纯向量不得为默认

- **WHEN** 集合未显式将 `search_mode` 设为 `vector`
- **THEN** 场景 RAG SHALL NOT 默认仅走向量检索
