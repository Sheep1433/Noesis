## Why

测试用例生成能力正在逐步退场，但服务启动仍会自动创建 `requirement_docs` 与 `test_case_docs`，使所有环境都出现与主产品无关的默认知识库。第一步应停止这种隐式创建，让知识库只来自用户操作或明确的业务流程。

## What Changes

- 服务启动时不再自动创建 `requirement_docs` 和 `test_case_docs`。
- 保留对 Qdrant 已有集合的 PostgreSQL 配置补全，避免影响用户现有知识库。
- 暂不删除测试用例上传、生成、检索代码及集合名称配置；后续按独立变更继续退场。
- 不主动删除环境中已经存在的两个集合，避免未经确认删除数据。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `knowledge-base`: 知识库启动初始化不再创建测试用例链路专用的默认集合，只同步已有集合配置。

## Impact

- 影响后端启动生命周期、知识库启动同步逻辑及相关测试。
- 不修改 `/api/kb`、聊天 SSE 或现有集合读写 API。
- 已存在的 `requirement_docs`、`test_case_docs` 仍保留，需用户或后续迁移显式删除。
