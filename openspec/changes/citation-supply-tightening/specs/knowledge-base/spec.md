## MODIFIED Requirements

### Requirement: 来源元数据 SHALL 支持 Prompt 引用与可点击回源

知识库检索结果 SHALL 向 Agent 提供可读的文件名、Collection、excerpt 与可用 locator，供模型生成编号引用和参考资料列表。客户端 SHALL 将唯一匹配的文件名与 Collection 转换为受认证保护的 Collection 文档路由；面向用户的 Prompt SHALL NOT 要求模型输出内部 identity。KB 工具输出 SHALL NOT 包含可引用性布尔门禁字段（如 `citable`）；仅具稳定版本身份（document_id / document_version_id / segment_id 齐全）的命中 SHALL 输出 `citation_ref`，无稳定版本身份的命中 SHALL NOT 输出 `citation_ref`（模型依据既有 prompt 约定对未提供来源的结果不添加引用）。web 检索与抓取工具输出同样 SHALL NOT 包含可引用性布尔门禁字段。检索结果是否进入来源面板 SHALL 由注册层证据身份校验（EvidenceEnvelope）独立判定，SHALL NOT 依赖工具输出的门禁字段。

#### Scenario: 检索结果包含页码

- **WHEN** 知识库命中包含 page locator
- **THEN** 工具结果 SHALL 保留页码和文件名
- **AND** 模型 MAY 在 `### 参考资料` 中展示该定位信息

#### Scenario: 未版本化命中不供引用 ref

- **WHEN** KB 命中缺少 document_id / document_version_id / segment_id 任一（legacy_unversioned）
- **THEN** 工具结果 SHALL NOT 输出 `citation_ref`，SHALL NOT 输出 `citable` 字段
- **AND** 该命中 SHALL 因证据身份校验不通过而不进入来源面板

#### Scenario: 工具输出不含门禁字段

- **WHEN** `web_search` / `web_fetch` / `search_knowledge_base` 返回结果
- **THEN** 每条结果的模型可见输出 SHALL NOT 包含 `citable` 布尔字段
- **AND** 可引用性准入 SHALL 由注册层证据身份校验独立承担
