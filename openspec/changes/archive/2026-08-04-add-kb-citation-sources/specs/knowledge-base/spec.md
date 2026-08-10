## ADDED Requirements

### Requirement: 知识库命中 SHALL 提供稳定 evidence identity

知识库检索门面 SHALL 为每个最终命中提供 versioned evidence envelope，至少包含 `document_id`、`document_version_id`、`segment_id`、title、受限 excerpt 与可选 typed locator。KB 工具 SHALL NOT 向 Agent 输出 run-local `evidence_id`；该 ID 仅由平台消息构建器在登记检索结果时分配，用于检索结果块的稳定去重和恢复。三层持久身份 SHALL 跨 hybrid、BM25、vector 与 rerank 路径保留。数组位置、chunk index、内容 hash 和 Qdrant point id SHALL NOT 作为长期公开 citation identity。

#### Scenario: BM25 命中进入 rerank

- **WHEN** hybrid 检索的 BM25 命中经过 rerank 后进入最终结果
- **THEN** 最终 hit SHALL 保留相同 document、version、segment identity 与 locator

#### Scenario: 同一轮重复命中

- **WHEN** 多次或并行检索命中同一 document version segment
- **THEN** 平台 retrieval manifest SHALL 能确定性识别同一 evidence，并保留关联的 tool call
- **AND** 多次或并行工具调用 SHALL 复用同一个 run-local evidence id

### Requirement: 来源元数据 SHALL 支持 Prompt 引用与可点击回源

知识库检索结果 SHALL 向 Agent 提供可读的文件名、Collection、excerpt 与可用 locator，供模型生成编号引用和参考资料列表。客户端 SHALL 将唯一匹配的文件名与 Collection 转换为受认证保护的 Collection 文档路由；面向用户的 Prompt SHALL NOT 要求模型输出内部 identity。

#### Scenario: 检索结果包含页码

- **WHEN** 知识库命中包含 page locator
- **THEN** 工具结果 SHALL 保留页码和文件名
- **AND** 模型 MAY 在 `### 参考资料` 中展示该定位信息
