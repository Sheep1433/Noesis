## Why

`noesis_server.domain.auth` 目前直接 import SQLAlchemy、`AsyncSession` 与 ORM 模型，导致领域规则、事务和持久化混在一起，纯逻辑无法脱离 PostgreSQL 测试。平台命名空间刚完成收敛，现在应固定 auth 的依赖方向，避免新的认证能力继续堆叠在 ORM service 上。

## What Changes

- 引入不依赖 SQLAlchemy/FastAPI/Pydantic 的 auth domain entity、策略与 repository port。
- 在 `noesis_server.infrastructure.database.repositories` 提供 SQLAlchemy repository adapter 和 entity/ORM 映射。
- 将 session、注册码、登录/注册用例归入 application services，由 API/FastAPI dependency 组装 repository。
- 统一事务边界：repository 不自行 commit，application service 在一个用例结束时 commit/rollback。
- 删除 domain 对 ORM、数据库 session 和平台异常类型的静态依赖，并增加 AST 边界测试。
- 保持 `/api/user`、Cookie、CSRF、数据库表结构及错误响应兼容；不做数据库迁移。

## Capabilities

### New Capabilities

- `auth-domain-boundary`: auth 领域实体、策略、repository port、SQLAlchemy adapter 与依赖方向。

### Modified Capabilities

- `user-platform`: 登录、注册、Session Cookie、CSRF 与邀请码的外部行为保持兼容，但内部由 application service 经 repository port 完成。

## Impact

- 代码：`backend/noesis_server/domain/auth/**`、`services/auth/**`、`infrastructure/database/repositories/**`、认证 API/middleware 与相关测试。
- API：`/api/user` 等既有路径和响应不变，无 breaking HTTP 变更。
- 数据：继续使用 `t_user` / `t_user_session`，无需 Alembic migration。
- 测试：新增纯 domain 单元测试、repository adapter 测试和认证回归。
