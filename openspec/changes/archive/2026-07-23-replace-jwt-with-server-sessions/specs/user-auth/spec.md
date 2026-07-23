## MODIFIED Requirements

### Requirement: 用户登录颁发令牌

系统 SHALL 通过 `POST /api/auth/login` 接受 `application/x-www-form-urlencoded` 的用户名与密码，校验凭据后创建一个可撤销的服务端会话，并以 HttpOnly Session Cookie 建立浏览器登录态。成功响应 SHALL 返回统一成功结构、最小用户资料、当前会话元数据和 CSRF Token，且 SHALL NOT 返回 JWT、Bearer Token、刷新 Token 或原始 Session ID。旧 `POST /api/user/login` SHALL 不再提供。

#### Scenario: 凭据正确

- **WHEN** 客户端提交已存在用户且密码正确
- **THEN** HTTP 状态码为 200，业务 `code` 为 200，`success` 为 true
- **AND** 响应 SHALL 设置符合安全属性的 HttpOnly Session Cookie
- **AND** 响应体 SHALL 包含非空的用户资料、当前会话元数据与 CSRF Token，且不包含可复用认证令牌

#### Scenario: 凭据错误

- **WHEN** 用户名不存在或密码错误
- **THEN** 系统 SHALL 不创建会话或设置登录 Cookie，并返回与全局异常处理一致的失败响应
- **AND** 响应 SHALL NOT 泄露密码、用户枚举信息或内部堆栈

### Requirement: 受保护接口的身份识别

系统 SHALL 对声明依赖当前用户的接口，仅从 HttpOnly Session Cookie 解析服务端会话并加载用户标识。会话不存在、已撤销、空闲过期或超过绝对过期时，系统 SHALL 返回 401 及统一未授权结构；系统 SHALL NOT 接受 Authorization Bearer JWT 作为替代身份凭据。

#### Scenario: 有效会话访问受保护资源

- **WHEN** 请求携带有效、未撤销且未过期的 Session Cookie 访问受保护资源
- **THEN** 系统 SHALL 识别该会话所属用户并按既有资源隔离规则处理请求
- **AND** 系统 SHALL 按会话续期策略更新最后活跃时间和 Cookie 有效期，且不得超过绝对过期时间

#### Scenario: 缺少、失效或撤销的会话

- **WHEN** 请求受保护资源但未携带有效 Session Cookie
- **THEN** HTTP 状态码为 401，业务码与 `ResponseUtil.unauthorized` 约定一致
- **AND** 系统 SHALL NOT 以 Bearer Token 恢复该请求的身份

## ADDED Requirements

### Requirement: 邀请码控制的用户注册

系统 SHALL 通过 `POST /api/auth/register` 接受用户名、密码、可选手机号和 6 位数字邀请码。系统 SHALL 仅在邀请码与管理员用户记录中当前全局邀请码摘要匹配时创建用户；邀请码明文 SHALL NOT 持久化或通过查询接口返回，且邀请码 SHALL NOT 因注册而消耗。注册成功后系统 SHALL 创建可撤销的服务端会话、设置 HttpOnly Session Cookie，并返回与登录一致的最小用户资料、会话元数据和 CSRF Token。

#### Scenario: 使用有效全局邀请码注册

- **WHEN** 未登录客户端提交满足校验规则的用户名、密码和有效的 6 位数字邀请码
- **THEN** 系统 SHALL 创建用户且保留该邀请码可供后续注册使用
- **AND** 响应 SHALL 设置 Session Cookie 并返回 HTTP 200、业务 `code` 200

#### Scenario: 邀请码无效

- **WHEN** 未登录客户端提交格式不正确或与当前邀请码不匹配的邀请码
- **THEN** 系统 SHALL 不创建用户、不设置登录 Cookie，且返回统一注册失败响应

#### Scenario: 多个用户使用同一邀请码

- **WHEN** 多个注册请求使用同一有效全局邀请码
- **THEN** 系统 SHALL 分别创建满足用户名唯一约束的用户，且邀请码保持有效直至轮换

#### Scenario: 管理员轮换邀请码

- **WHEN** 管理员在服务端执行邀请码轮换命令
- **THEN** 系统 SHALL 更新管理员用户记录中的邀请码摘要，并仅在该命令输出中显示一次新的 6 位邀请码明文
- **AND** 旧邀请码 SHALL 立即失效

### Requirement: 页面重建时恢复会话

系统 SHALL 提供 `GET /api/auth/session`，供 SPA 在路由鉴权前验证浏览器 Cookie 并恢复内存登录态。该接口成功时 SHALL 返回最小用户资料、当前会话元数据和可用于本会话状态变更的 CSRF Token；前端 SHALL 不把认证令牌持久化到 `sessionStorage`、`localStorage` 或其他 JavaScript 可读持久化存储。

#### Scenario: 移动端后台回收后恢复

- **WHEN** 浏览器页面重建且 Session Cookie 仍对应有效会话
- **THEN** 前端 SHALL 在进入受保护路由前调用 `GET /api/auth/session`
- **AND** 系统 SHALL 恢复登录态而不要求用户重新输入凭据

#### Scenario: 会话已失效

- **WHEN** 前端启动时 `GET /api/auth/session` 返回 401
- **THEN** 前端 SHALL 清除内存用户状态并进入登录页
- **AND** 前端 SHALL NOT 将网络连接失败或服务端 5xx 误判为登录失效

### Requirement: 会话撤销与设备管理

系统 SHALL 提供 `POST /api/auth/logout` 撤销当前会话、`POST /api/auth/logout-all` 撤销当前用户全部会话、`GET /api/auth/sessions` 列出当前用户未撤销会话，以及 `DELETE /api/auth/sessions/{session_id}` 撤销指定会话。会话列表 SHALL 不暴露原始 Session ID 或认证秘密。

#### Scenario: 退出当前会话

- **WHEN** 已登录用户调用 `POST /api/auth/logout` 且通过 CSRF 校验
- **THEN** 系统 SHALL 撤销当前服务端会话并清除浏览器 Session Cookie
- **AND** 该 Cookie 随后的受保护请求 SHALL 返回 401

#### Scenario: 撤销另一台设备

- **WHEN** 已登录用户删除其会话列表中的另一个有效会话
- **THEN** 系统 SHALL 仅撤销目标会话
- **AND** 目标设备的后续受保护请求 SHALL 返回 401，而当前会话仍保持有效

### Requirement: Cookie 会话的 CSRF 防护

系统 SHALL 对所有通过 Cookie Session 鉴权的非安全 HTTP 方法验证会话绑定 CSRF Token；Token 可由 `X-CSRF-Token` Header 提供，聊天停止 Beacon 可在 JSON body 的 `csrf_token` 字段提供。缺失或无效 CSRF Token SHALL 返回 403 统一失败响应，且 SHALL NOT 撤销有效 Session 或将其伪装为 401。

#### Scenario: 常规状态变更携带 CSRF Header

- **WHEN** 已登录客户端向受保护接口发起 POST、PUT、PATCH 或 DELETE 请求并携带有效 `X-CSRF-Token`
- **THEN** 系统 SHALL 执行业务请求

#### Scenario: 聊天 stop Beacon 携带 Body Token

- **WHEN** 流式生成期间页面关闭，客户端通过 Beacon 调用 `/api/chat/sessions/{session_id}/stop` 且 body 含有效 `csrf_token`
- **THEN** 系统 SHALL 将该请求作为用户主动停止处理
- **AND** 聊天流的停止、SSE 收尾及 assistant 持久化语义 SHALL 保持不变

#### Scenario: CSRF 校验失败

- **WHEN** 已登录客户端对受保护资源发起非安全请求但未携带有效 CSRF Token
- **THEN** 系统 SHALL 返回 HTTP 403 与统一失败结构
- **AND** 系统 SHALL 不执行目标业务操作且不撤销当前会话
