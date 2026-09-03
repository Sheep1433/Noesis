# user-platform Specification

## Purpose

本能力规定 **用户平台横切**：Session Cookie 认证与设备会话、Auth 域与持久化隔离、用户 MCP 配置合并、以及 PostgreSQL 业务库与 LangGraph checkpoint 库。聊天 API 行为见 `platform-chat`；设置控制面见 `user-settings`；部署见 `container-deployment`。
## Requirements
### Requirement: Session Cookie 认证

系统 SHALL 通过 `POST /api/auth/login`（form-urlencoded）校验凭据，创建可撤销服务端会话，设置 HttpOnly Session Cookie，并返回用户资料、会话元数据与 CSRF Token。**SHALL NOT** 返回 JWT / Bearer / 刷新 Token / 原始 Session ID。旧 `POST /api/user/login` **SHALL NOT** 再提供。

受保护接口 SHALL 仅从 Session Cookie 识别用户；缺失/撤销/过期 SHALL 401。**SHALL NOT** 接受 Authorization Bearer JWT 作为替代凭据。Session 领域规则 SHALL 与 ORM/数据库隔离，持久化 SHALL 经 repository port 与 SQLAlchemy adapter 完成。

#### Scenario: 登录成功

- **WHEN** 用户名密码正确
- **THEN** 200 + Set-Cookie + CSRF，响应体无 JWT

#### Scenario: 无 Cookie 访问

- **WHEN** 无有效 Session Cookie 访问受保护资源
- **THEN** 401

### Requirement: 注册与邀请码

`POST /api/auth/register` SHALL 在邀请码匹配时创建用户并建立会话；邀请码明文 **SHALL NOT** 持久化或经查询接口返回。邀请码摘要的读取与更新 SHALL 经 user repository port 完成，domain SHALL NOT 直接访问 ORM 用户记录。

#### Scenario: 邀请码错误

- **WHEN** 邀请码不匹配
- **THEN** SHALL 拒绝注册且不创建用户

### Requirement: Auth 域与框架和持久化隔离

系统 SHALL 使 `noesis.auth` 的 entity、policy 与 repository port 可在不加载 FastAPI、SQLAlchemy、ORM model 或平台 exception 的环境中导入和测试。Session 领域规则 SHALL 与 ORM/数据库隔离。

#### Scenario: 静态边界检查

- **WHEN** AST 扫描 `noesis.auth`
- **THEN** SHALL 不存在对 FastAPI、SQLAlchemy、`noesis.storage` ORM model 或 `noesis.services` 的 import

#### Scenario: 无数据库测试领域规则

- **WHEN** 使用内存 fake repository 执行 Session 创建、过期、续期、CSRF 或邀请码验证测试
- **THEN** SHALL 无需数据库 engine、ORM model 或 FastAPI app

### Requirement: Auth 持久化经窄 Repository Port 与事务边界

用户与会话的读取写入 SHALL 通过 auth 专用 repository port 完成，SQLAlchemy adapter SHALL 位于 `noesis.repositories`，且 SHALL NOT 将 ORM 实例返回给 domain 或 API。Auth repository SHALL NOT 自行 commit 或 rollback；application service SHALL 在完整用例成功后 commit，并在预期数据库冲突时 rollback 和映射业务错误。

#### Scenario: 注册唯一键冲突

- **WHEN** PostgreSQL 返回用户名唯一键冲突
- **THEN** application service SHALL rollback 并返回既有用户名冲突业务错误

### Requirement: 用户会话记录查询

`POST /api/user/query_user_record`（或现行路径）SHALL 支持标题搜索与分页，仅返回当前用户可见会话。

#### Scenario: 隔离

- **WHEN** 用户 A 查询
- **THEN** **SHALL NOT** 返回用户 B 的会话

### Requirement: 用户 MCP 配置

系统 SHALL 持久化用户 MCP server 配置，并在 Agent 装配时与平台默认合并；用户配置 **SHALL NOT** 覆盖平台安全禁止项（若有黑名单）。CRUD API SHALL 需登录。

#### Scenario: 合并可见

- **WHEN** 用户添加自定义 MCP server 后启动 FAULT_OPERATION 或启用该 server 的会话
- **THEN** 工具列表 SHALL 含合并后的可用 server（在连接成功前提下）

### Requirement: PostgreSQL 业务库

用户、聊天会话/消息/附件元数据、知识库集合配置等关系数据 SHALL 使用 PostgreSQL；启动时 SHALL 跑 Alembic 并连通校验。

#### Scenario: 无法连接则启动失败

- **WHEN** 业务库不可达
- **THEN** 后端 SHALL 启动失败并记录可定位错误

### Requirement: Checkpoint 独立库

LangGraph checkpoint SHALL 使用独立 PostgreSQL 库/逻辑隔离；初始化 **SHALL NOT** 修改业务表。

#### Scenario: 跨实例恢复

- **WHEN** 两实例共享同一 checkpoint 库与 thread id
- **THEN** 任一实例 SHALL 能读取已提交 checkpoint

### Requirement: 用户作用域设置 API SHALL 统一鉴权、隔离与 secret 语义
新增设置 API SHALL 使用有效 Cookie Session，非安全方法 SHALL 校验 CSRF，所有读写 SHALL 限于当前用户。secret read model SHALL 只返回 configured、可选后缀和更新时间；写命令 SHALL 区分 keep、replace、clear。系统 SHALL NOT 使用 Bearer JWT 作为这些 API 的身份凭据。

#### Scenario: 跨用户设置访问
- **WHEN** 用户 A 使用用户 B 的设置资源 id 发起读取或修改
- **THEN** 系统 SHALL 返回 404 且不披露资源是否存在

### Requirement: 用户设置导入 SHALL 按域校验和事务化
导入 apply SHALL 重新校验 preview 使用的版本和当前状态，并按设置域事务化写入；某域失败 SHALL 回滚该域且不留下部分 secret 变更。所有成功或失败的 apply SHALL 形成脱敏审计。

#### Scenario: 导入时状态已变化
- **WHEN** preview 后目标设置已被其它请求修改
- **THEN** apply SHALL 拒绝冲突域并要求重新预览，而不是静默覆盖

### Requirement: 用户设置 SHALL NOT 托管 Provider 凭据

普通用户 API SHALL NOT 提供 Provider 连接、Base URL、API Key、远程模型发现或模型用途绑定的创建、更新、删除和测试能力。历史 Provider 凭据 SHALL 经迁移从业务库删除。

#### Scenario: 请求旧 Provider API
- **WHEN** 已认证用户请求 `/api/user/providers`
- **THEN** 系统 SHALL 返回 404 且不得读取或写入 Provider 数据

#### Scenario: 数据迁移
- **WHEN** 部署执行移除用户 Provider 的数据库迁移
- **THEN** 用户 Provider 连接与用途绑定数据 SHALL 被删除

### Requirement: 用户标识 SHALL 使用字符串 UUID

系统 SHALL 以 UUID 字符串作为用户唯一标识，在数据库、会话、Service 与 API 层保持单一表示；SHALL NOT 在自增整数与字符串之间维护双表示。

#### Scenario: 新用户注册
- **WHEN** 新用户创建账户
- **THEN** 系统 SHALL 生成 UUID 作为用户标识并在所有关联表中以字符串引用

#### Scenario: 存量数据迁移
- **WHEN** 已有数据库升级到本版本
- **THEN** Alembic SHALL 将既有整型 user id 转换为 UUID 并保持全部关联关系

