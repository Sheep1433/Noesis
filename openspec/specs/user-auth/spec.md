## Purpose

本能力描述 Noesis 用户身份认证与「用户记录」查询：基于 JWT 的会话态、OAuth2 密码流登录端点，以及已登录用户按条件分页检索聊天会话摘要的行为边界，供前后端与测试对齐。

## Requirements

### Requirement: 用户登录颁发令牌

系统 SHALL 通过 `POST /api/user/login` 接受 `application/x-www-form-urlencoded` 的 OAuth2 密码流表单（username、password），校验凭据后返回统一成功结构，并在 `data.token` 中携带 JWT。

#### Scenario: 凭据正确

- **WHEN** 客户端提交已存在用户且密码正确
- **THEN** HTTP 状态码为 200，业务 `code` 为 200，`success` 为 true，且响应体包含非空的 `data.token`

#### Scenario: 凭据错误

- **WHEN** 用户名不存在或密码错误
- **THEN** 系统 SHALL 不颁发令牌，并返回与全局异常处理一致的失败响应（不得泄露密码或内部堆栈）

### Requirement: 受保护接口的身份识别

系统 SHALL 对声明依赖当前用户的接口，从 Authorization Bearer JWT 中解析用户标识；令牌无效或过期时返回 401 及统一未授权结构。

#### Scenario: 缺少或非法令牌

- **WHEN** 请求受保护资源但未携带有效 Bearer token
- **THEN** HTTP 状态码为 401，业务码与 `ResponseUtil.unauthorized` 约定一致

### Requirement: 用户会话记录查询

系统 SHALL 提供 `POST /api/user/query_user_record`，支持按标题模糊搜索、按会话 id 过滤与分页，并返回会话列表及解析后的问答类型等前端展示所需字段。

#### Scenario: 分页查询

- **WHEN** 已登录用户提交合法分页参数与可选 search_text
- **THEN** 返回 200、列表数据与总条数，且仅包含当前用户可见的会话
