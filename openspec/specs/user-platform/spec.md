# user-platform Specification

## Purpose

本能力规定 **用户平台横切**：Session Cookie 认证与设备会话、用户 MCP 配置合并、以及 PostgreSQL 业务库与 LangGraph checkpoint 库。聊天 API 行为见 `platform-chat`；部署见 `container-deployment`。

## Requirements

### Requirement: Session Cookie 认证

系统 SHALL 通过 `POST /api/auth/login`（form-urlencoded）校验凭据，创建可撤销服务端会话，设置 HttpOnly Session Cookie，并返回用户资料、会话元数据与 CSRF Token。**SHALL NOT** 返回 JWT / Bearer / 刷新 Token / 原始 Session ID。旧 `POST /api/user/login` **SHALL NOT** 再提供。

受保护接口 SHALL 仅从 Session Cookie 识别用户；缺失/撤销/过期 SHALL 401。**SHALL NOT** 接受 Authorization Bearer JWT 作为替代凭据。

#### Scenario: 登录成功

- **WHEN** 用户名密码正确
- **THEN** 200 + Set-Cookie + CSRF，响应体无 JWT

#### Scenario: 无 Cookie 访问

- **WHEN** 无有效 Session Cookie 访问受保护资源
- **THEN** 401

### Requirement: 注册与邀请码

`POST /api/auth/register` SHALL 在邀请码匹配时创建用户并建立会话；邀请码明文 **SHALL NOT** 持久化或经查询接口返回。

#### Scenario: 邀请码错误

- **WHEN** 邀请码不匹配
- **THEN** SHALL 拒绝注册且不创建用户

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
