## Context

`domain/auth/session.py` 与 `registration_invite.py` 当前直接查询 `TUser` / `TUserSession`、接收 `AsyncSession` 并自行 commit。`LoginService` / `UserService` 也重复查询用户，使领域规则、应用用例、HTTP 状态和 SQLAlchemy 生命周期相互缠绕。

## Goals / Non-Goals

**Goals:**

- `noesis_server.domain.auth` 仅包含 entity、值规则、密码/摘要/过期/CSRF 等纯逻辑以及 repository Protocol。
- application service 负责登录、注册、session、邀请码用例和事务边界。
- SQLAlchemy adapter 独占 ORM 查询与 entity 映射。
- 保持 Cookie、CSRF、错误消息、表结构和 API 路径兼容。

**Non-Goals:**

- 不引入通用 Repository 基类、完整 UnitOfWork 框架或 DI 容器。
- 不修改密码算法、Session 时效配置、邀请码产品规则或数据库 schema。
- 不在本 change 纯化 chat/KB 等其他 domain。

## Decisions

1. **使用针对 auth 的窄 repository port**：`UserRepository` 与 `SessionRepository` 只声明用例需要的方法，避免 CRUD 泛型泄露 ORM 语义。Protocol 位于 `domain/auth/ports.py`。
2. **domain entity 使用 dataclass**：`AuthUser`、`AuthSession` 只保存业务字段，不继承 SQLAlchemy/Pydantic。过期、剩余时间、CSRF 比较等规则位于 entity/policy，可用固定时钟测试。
3. **SQLAlchemy adapter 显式映射**：`infrastructure/database/repositories/auth.py` 在 ORM 与 entity 间转换；调用方不得收到 `TUser` / `TUserSession`。
4. **应用层拥有 commit/rollback**：repository 仅 query/add/flush；`services/auth` 在成功用例末尾 commit，冲突时 rollback。先复用调用方注入的 `AsyncSession`，不引入第二套 UoW abstraction。
5. **FastAPI 依赖只在 service/API 边缘**：Cookie/Request/Depends 仍由 `UserService` 与 API 处理；domain 不 import FastAPI、平台 schema 或平台 exception。
6. **渐进迁移但不保留旧 domain service shim**：调用方一次性迁至 `services.auth`，原 `domain.auth.SessionService` / `RegistrationInviteService` 删除，避免双权威路径。

## Risks / Trade-offs

- [映射遗漏 ORM 字段] → entity 覆盖当前认证实际读取/写入字段，并增加 adapter round-trip 测试。
- [事务行为变化] → 用例级测试断言 commit/rollback；repository 禁止 commit 的 AST/Mock 测试。
- [测试 patch 路径断裂] → 仓内一次性迁移到 application service/adapter 权威路径，不保留兼容 shim。
- [抽象过度] → 仅两个窄 Protocol，不引入 generic repository 或全局 DI container。

## Migration Plan

1. 增加 entity/policy/ports 与纯 domain 测试。
2. 增加 SQLAlchemy repositories 和映射测试。
3. 新建 `services/auth` 用例，迁移 API、middleware、LoginService/UserService 调用。
4. 删除旧 domain ORM service，运行边界测试与认证全量回归。

回滚只需恢复旧服务实现；数据库 schema 与数据均不变化。

## Open Questions

无。本期明确采用轻量 repository + 调用方 `AsyncSession`，不引入完整 UnitOfWork。
