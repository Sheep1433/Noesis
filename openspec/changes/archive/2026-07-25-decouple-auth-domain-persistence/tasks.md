## 1. Pure auth domain

- [x] 1.1 增加 AuthUser/AuthSession entity、session policy 与 repository Protocol
- [x] 1.2 增加无 SQLAlchemy/FastAPI 的 domain 单元测试与 AST 边界测试

## 2. Persistence adapters

- [x] 2.1 实现 SQLAlchemy user/session repository 与 ORM/entity 映射
- [x] 2.2 验证 repository 不提交事务并覆盖查询、写入与映射测试

## 3. Application services

- [x] 3.1 将 Session 与注册码用例迁到 `noesis_server.services.auth`
- [x] 3.2 将登录/注册、UserService、API 与 middleware 迁到 repository-backed application service
- [x] 3.3 删除旧 domain ORM service 和旧 import/patch 路径

## 4. Verification

- [x] 4.1 运行认证定向测试、OpenSpec 校验和 backend 全量回归
- [x] 4.2 更新 backend 架构文档与 NOTES
