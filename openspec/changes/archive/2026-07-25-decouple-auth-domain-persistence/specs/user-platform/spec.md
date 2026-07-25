## MODIFIED Requirements

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
