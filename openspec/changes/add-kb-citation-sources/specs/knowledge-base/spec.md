## ADDED Requirements

### Requirement: 知识库命中 SHALL 提供稳定 evidence identity

知识库检索门面 SHALL 为每个最终命中提供 versioned evidence envelope，至少包含 `document_id`、`document_version_id`、`segment_id`、title、受限 excerpt 与可选 typed locator。KB 工具 SHALL NOT 分配 run-local `evidence_id`；该 ID 由 Harness/runtime 的 run 级 retrieval manifest 在登记稳定 identity 时统一分配。三层持久身份 SHALL 跨 hybrid、BM25、vector 与 rerank 路径保留。数组位置、chunk index、内容 hash 和 Qdrant point id SHALL NOT 作为长期公开 citation identity。

#### Scenario: BM25 命中进入 rerank

- **WHEN** hybrid 检索的 BM25 命中经过 rerank 后进入最终结果
- **THEN** 最终 hit SHALL 保留相同 document、version、segment identity 与 locator

#### Scenario: 同一轮重复命中

- **WHEN** 多次或并行检索命中同一 document version segment
- **THEN** retrieval manifest SHALL 能确定性识别同一 evidence，并保留关联的 tool call
- **AND** 多次或并行工具调用 SHALL 复用同一个 run-local evidence id

### Requirement: 来源定位 SHALL 支持版本化精确失败

知识库服务 SHALL 能按 `document_id + document_version_id + segment_id` 读取原 evidence，并 MAY 使用带 `type` discriminator 的 page、char、bbox 或 header locator 定位 viewer。page SHALL 使用 1-based 页码，char SHALL 使用原始 segment Unicode code point 左闭右开范围，bbox SHALL 使用页面归一化坐标。旧版本或 segment 不存在时，系统 SHALL 返回明确的 stale/missing 结果，SHALL NOT 用语义相似片段替代原引用。

#### Scenario: 文档重新摄取

- **WHEN** 当前文档已有新版本但 citation 指向的旧 version 仍存在
- **THEN** 来源解析 SHALL 返回旧 version 的原 segment

#### Scenario: 旧版本已清理

- **WHEN** citation 指向的 document version 或 segment 已不存在
- **THEN** 来源解析 SHALL 标记 stale 或 missing，而不是返回新版本的相似 chunk
