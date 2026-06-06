## Purpose

本增量规格在不影响既有 Qdrant 知识库 REST 语义的前提下，将 **检索默认参数** 及与入库侧 **processing_params** 相关的验收要求并入 `knowledge-base` 能力，与 `rag-chunking-pipeline` 能力相互配合。

## ADDED Requirements

### Requirement: 集合级检索默认参数

系统 SHALL 支持为每个知识库集合持久化可选的 **query_params**（或项目中等价命名的配置块），其中包括适用于该集合的向量检索默认选项（例如返回条数上限、分数阈值等与当前 Qdrant 实现一致的字段）；公开检索接口在调用方未显式指定对应键时 SHALL 使用该集合的默认值。

#### Scenario: 未传 top_k 时使用集合默认

- **WHEN** 客户端调用既有向量检索接口且未在请求中指定等价于 top_k 的参数，而该集合已配置较大的默认 top_k
- **THEN** 实际检索所使用的条数上限 SHALL 等于集合配置中的默认值，且 SHALL 不改变未配置集合时的向后兼容行为

### Requirement: API 向后兼容的参数扩展

对 `knowledge-base` 能力已暴露的集合创建、文档上传或更新类端点，系统 MAY 增设可选的请求/响应字段以承载 processing_params 与 query_params；对未携带此类字段的旧客户端 SHALL 保持成功路径与语义不变。

#### Scenario: 旧客户端无新字段仍可上传

- **WHEN** 客户端使用与本能力落地前相同的最小必填字段调用上传接口
- **THEN** 系统 SHALL 按迁移方案中的默认空配置完成处理，且不 SHALL 因缺少可选参数而返回 4xx（除非原有校验本就失败）

### Requirement: 检索与入库配置一致性校验

系统在创建或更新集合的嵌入模型与维度配置时 SHALL 在执行入库索引前校验与目标 collection 向量维度一致；当 query_params / processing_params 中声明与向量无关的选项时 SHALL NOT 破坏上述维度约束。若检测到因配置变更导致与已索引数据不兼容的用户操作，SHALL 返回 409 或项目约定的冲突响应，且 SHALL 不与「HTTP 与业务码一致」的项目规范相冲突。

#### Scenario: 不兼容的配置变更被拒

- **WHEN** 用户尝试在未重建索引的前提下更改编排规则导致与现有 Qdrant collection 向量配置冲突（由实现定义的检测点触发）
- **THEN** 系统 SHALL 拒绝该次更新并给出可行动的错误信息，且 SHALL NOT 部分写入不一致元数据
