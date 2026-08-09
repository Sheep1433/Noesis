## ADDED Requirements

### Requirement: 用户设置 SHALL NOT 托管 Provider 凭据

普通用户 API SHALL NOT 提供 Provider 连接、Base URL、API Key、远程模型发现或模型用途绑定的创建、更新、删除和测试能力。历史 Provider 凭据 SHALL 经迁移从业务库删除。

#### Scenario: 请求旧 Provider API
- **WHEN** 已认证用户请求 `/api/user/providers`
- **THEN** 系统 SHALL 返回 404 且不得读取或写入 Provider 数据

#### Scenario: 数据迁移
- **WHEN** 部署执行移除用户 Provider 的数据库迁移
- **THEN** 用户 Provider 连接与用途绑定数据 SHALL 被删除
