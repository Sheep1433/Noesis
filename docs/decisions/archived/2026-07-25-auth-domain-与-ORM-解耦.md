# 决策：auth domain 与 ORM 解耦

状态：implemented
日期：2026-07-25
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- `noesis_server.domain.auth` 只保留 `AuthUser` / `AuthSession`、policy 和 repository Protocol，不 import FastAPI、SQLAlchemy、ORM 或平台异常。
- `noesis_server.infrastructure.database.repositories.auth` 负责 ORM/entity 显式映射；repository 只 flush，不 commit/rollback。
- Session、邀请码、登录注册事务边界位于 `noesis_server.services.auth` / `LoginService`；Cookie transport 位于 `noesis_server.api.auth_cookie`。
- 数据库表、Session Cookie、CSRF 与 `/api/auth` 外部行为不变。
