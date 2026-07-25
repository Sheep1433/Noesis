# auth-domain-boundary Specification

## Purpose
TBD - created by archiving change decouple-auth-domain-persistence. Update Purpose after archive.
## Requirements
### Requirement: Auth domain 与框架和持久化隔离

系统 SHALL 使 `noesis_server.domain.auth` 的 entity、policy 与 repository port 可在不加载 FastAPI、SQLAlchemy、ORM model 或平台 exception 的环境中导入和测试。

#### Scenario: 静态边界检查

- **WHEN** AST 扫描 `noesis_server/domain/auth`
- **THEN** SHALL 不存在对 FastAPI、SQLAlchemy、`noesis_server.models`、`noesis_server.infrastructure`、`noesis_server.services` 或 `noesis_server.exceptions` 的 import

### Requirement: Auth 持久化经窄 Repository Port

用户与会话的读取写入 SHALL 通过 auth 专用 repository port 完成，SQLAlchemy adapter SHALL 位于 `noesis_server.infrastructure.database.repositories`，且 SHALL NOT 将 ORM 实例返回给 domain 或 API。

#### Scenario: 无数据库测试领域规则

- **WHEN** 使用内存 fake repository 执行 Session 创建、过期、续期、CSRF 或邀请码验证测试
- **THEN** SHALL 无需数据库 engine、ORM model 或 FastAPI app

### Requirement: Application Service 拥有事务边界

Auth repository SHALL NOT 自行 commit 或 rollback；application service SHALL 在完整用例成功后 commit，并在预期数据库冲突时 rollback 和映射业务错误。

#### Scenario: 注册唯一键冲突

- **WHEN** PostgreSQL 返回用户名唯一键冲突
- **THEN** application service SHALL rollback 并返回既有用户名冲突业务错误
