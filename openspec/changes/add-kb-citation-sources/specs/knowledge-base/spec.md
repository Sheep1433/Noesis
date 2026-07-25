## ADDED Requirements

### Requirement: 知识库命中 SHALL 提供标准化 provenance locator

知识库检索门面 SHALL 为每个命中提供 versioned provenance locator，至少包含 collection、稳定 document key、chunk index 与可选内部 point id。locator SHALL 能跨 hybrid、BM25、vector 与 rerank 路径保留，且 SHALL NOT 将裸 `content_hash` 冒充可 retrieve 的 point id。

#### Scenario: BM25 命中进入 rerank

- **WHEN** hybrid 检索的 BM25 命中经过 rerank 后进入最终结果
- **THEN** 最终 hit SHALL 保留 document key、chunk index 与正确的可选 point id

#### Scenario: point id 缺失

- **WHEN** 历史 payload 没有可验证 point id
- **THEN** locator SHALL 将 point id 设为空，并保留 document key/chunk index fallback

### Requirement: 来源定位 SHALL 支持精确失败

知识库服务 SHALL 提供按私有 locator 读取片段的能力。定位失败时系统 SHALL 返回明确的 missing/stale 结果，SHALL NOT 在未声明的情况下用语义相似片段替代原引用。

#### Scenario: 文档重新分块

- **WHEN** 原 point 与 document key/chunk index 均无法精确匹配
- **THEN** 来源解析 SHALL 标记 stale 或 missing，而不是返回其它高相似度 chunk
