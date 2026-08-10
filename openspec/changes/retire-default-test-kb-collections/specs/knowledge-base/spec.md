## ADDED Requirements

### Requirement: 启动过程 SHALL 不创建测试用例专用默认知识库

系统启动 SHALL 只为 Qdrant 中已经存在的知识库补全缺失的 PostgreSQL 集合配置，SHALL NOT 自动创建 `requirement_docs`、`test_case_docs` 或其它业务专用默认集合。测试用例链路保留期间如需集合，必须由显式用户操作或运维动作创建。

#### Scenario: 新环境启动

- **WHEN** Qdrant 可用且当前没有任何集合
- **THEN** 服务启动 SHALL NOT 创建 `requirement_docs` 或 `test_case_docs`
- **AND** 服务 SHALL 正常完成启动

#### Scenario: 已有用户知识库缺少配置

- **WHEN** Qdrant 已有用户创建的集合且 PostgreSQL 中没有对应集合配置
- **THEN** 服务启动 SHALL 为该已有集合补全默认配置
- **AND** SHALL NOT 同时创建测试用例专用集合

#### Scenario: 环境已有历史默认集合

- **WHEN** Qdrant 已经存在 `requirement_docs` 或 `test_case_docs`
- **THEN** 本次启动 SHALL 保留其数据并可补全缺失配置
- **AND** SHALL NOT 自动删除该集合
