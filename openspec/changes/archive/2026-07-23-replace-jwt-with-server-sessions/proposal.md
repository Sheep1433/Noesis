## Why

移动浏览器可能在切换应用后回收页面进程，导致前端 `sessionStorage` 丢失；现有路由将其作为唯一登录态依据，即使 HttpOnly Cookie 仍有效也会错误地跳转登录页。当前 JWT、Bearer、Cookie 与滑动刷新并存，认证状态分散且无法可靠地撤销单台设备会话。

项目尚未有存量用户，适合现在一次性收敛为服务端会话模型，消除前端持久化令牌并为移动端恢复、设备管理和强制下线建立统一边界。

## What Changes

- **BREAKING**：以随机、不透明的服务端 Session 替代 JWT、`Authorization: Bearer`、`X-Refresh-Token` 和前端 `sessionStorage` Token。
- 登录成功后由服务端创建可撤销 Session，并仅通过 HttpOnly、安全 Cookie 保存 Session 标识；Session 状态、空闲续期与绝对过期由服务端存储管理。
- 新增 `/api/auth` 前缀的登录、当前会话恢复、退出当前会话、退出全部会话，以及会话设备查询和撤销接口；移除旧 `/api/user/login` JWT 登录契约。
- SPA 在路由守卫前向当前会话接口完成异步恢复；页面重建或移动端后台回收后，Cookie 有效时自动恢复登录态，只有服务端确认未授权才进入登录页。
- 对 Cookie 鉴权的非安全 HTTP 方法实施 CSRF 防护；支持会话撤销、设备元数据和审计所需的服务端状态。
- 新增仅凭有效全局邀请码的公开注册入口；6 位数字邀请码摘要存于管理员用户记录，由管理员在服务端轮换，注册成功后立即建立 Cookie Session。
- 已鉴权的聊天、知识库、Skill、附件等 `/api/*` 受保护接口统一从服务端 Session 获取当前用户，不再接受 Bearer JWT。

## Capabilities

### New Capabilities

- 无。

### Modified Capabilities

- `user-auth`：从 JWT 令牌认证改为服务端可撤销的 Cookie Session，并增加邀请码注册、会话恢复、CSRF 与设备会话管理要求。

## Impact

- 后端：认证依赖、`/api/auth` API、Session 持久化/缓存、配置、全局鉴权与 CSRF 中间件；受保护 API 的 `CurrentUser` 解析方式。
- 前端：`userStore`、`authHttp`、登录页、应用启动顺序和异步路由守卫；删除 Token Header 与 `sessionStorage` 登录态。
- 部署：生产 Cookie 安全属性、同源反向代理与 Session 存储的持久化/高可用配置。
- API：认证 API 迁至 `/api/auth`；全部 `/api/*` 受保护请求不再支持 Bearer JWT。这是破坏性变更；不保留历史兼容层。
