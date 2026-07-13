## Context

当前认证由 `backend/domain/auth/password.py` 签发 JWT；`frontend/src/store/business/userStore.ts` 将其写入 `sessionStorage`，`frontend/src/utils/authHttp.ts` 再作为 Bearer Header 发送。后端同时设置同有效期的 Cookie，并由 `backend/middleware/sliding_auth.py` 在每次成功请求上重签 JWT。移动端页面被系统回收后，`sessionStorage` 消失而 Cookie 仍可能有效，`frontend/src/router/index.ts` 会在未验证 Cookie 的情况下转至登录页。

本变更不需要迁移存量用户或 API 调用方，但涉及所有 `/api/*` 受保护请求、聊天 SSE 的 `POST` 请求与页面卸载时的 stop Beacon。认证数据属于安全边界，数据库写入继续遵守 API → Service → Domain 分层。

## Goals / Non-Goals

**Goals:**

- 用可撤销、可审计的服务端 Session 作为唯一登录态来源，确保移动端页面重建后能自动恢复登录。
- 消除浏览器 JavaScript 可读的长期认证令牌，以及 JWT、Bearer 与刷新 Header 的多套状态。
- 允许用户和管理员安全地查看、撤销当前设备或全部设备会话。
- 对 Cookie 鉴权的状态变更请求提供可验证的 CSRF 防护，并保持聊天主动停止的 Beacon 语义。

**Non-Goals:**

- 不提供 JWT、Bearer、旧 `/api/user/login` 或旧前端状态的兼容层。
- 不实现第三方 OAuth、单点登录、多租户身份源或设备指纹风控。
- 不改变聊天 SSE 事件、assistant 落库和断线处理的既有业务语义。

## Decisions

### 0. 管理员持有的全局可轮换邀请码控制公开注册

公开注册入口为 `POST /api/auth/register`，仅接受用户名、密码、可选手机号和 6 位数字邀请码。邀请码由服务端脚本以密码学随机数生成；数据库仅在持有邀请码的管理员 `t_user` 记录上保存 SHA-256 摘要和轮换时间。注册服务校验摘要但不消耗邀请码，因此同一码可用于多个用户注册，直到管理员主动轮换。

邀请码不提供公开查询或创建 API，避免把发码权限暴露为普通登录能力。管理员通过本地 CLI 轮换邀请码并仅在轮换时取得明文；未配置或无效的邀请码均返回统一的注册失败响应，避免泄露邀请码状态。注册成功后直接创建服务端 Session 并设置 Cookie，用户无需再次登录。

### 1. 采用不透明 Session ID + PostgreSQL 权威会话表

新增 `TUserSession`（或等价模型与迁移），字段至少包括：主键、`user_id`、Session ID 的 SHA-256 摘要、创建时间、最后活跃时间、空闲过期时间、绝对过期时间、撤销时间、设备名称、User-Agent 摘要和最近 IP。Cookie 中只保存来自 `secrets.token_urlsafe` 的高熵原始 Session ID；数据库绝不保存原值。

PostgreSQL 为权威存储，以保证重启、横向扩展与设备撤销的一致性；认证查询可在后续基于稳定的 Session 领域接口加入缓存，但本变更不引入第二个权威存储。相比无状态 JWT，这允许即时撤销；相比只使用 Redis，这避免缓存清空导致全员下线。

### 2. 单一 HttpOnly Session Cookie 与明确的期限策略

后端设置唯一的 `noesis_session` Cookie，属性为 `HttpOnly`、`Path=/`、生产环境 `Secure`、`SameSite=Lax`。Session 采用配置化空闲期限与绝对期限：每个成功认证请求在限频窗口内延长空闲过期并更新 Cookie；绝对过期永不延长。Cookie 的 Max-Age 不得超过剩余的空闲和绝对期限中的较小值。

选择 Cookie Session 而非短 JWT + Refresh Token：本应用是同源 SPA 与 FastAPI 服务，Cookie 可在页面重建后自动发送，且不会把可复用令牌暴露给 JavaScript。每个响应重签 JWT 既无法即时撤销，也会因流式响应的 Header 时机而使续期不可靠。

### 3. `/api/auth` 为唯一认证 API，前端启动时恢复会话

实现以下 API：`POST /api/auth/login`、`GET /api/auth/session`、`POST /api/auth/logout`、`POST /api/auth/logout-all`、`GET /api/auth/sessions`、`DELETE /api/auth/sessions/{session_id}`。它们使用统一 `ResponseUtil` 响应；登录与 session 恢复响应返回最小用户资料和当前会话元数据，不返回 Session ID。

`userStore` 只存内存用户资料、初始化状态与内存 CSRF Token，不持久化 Token。`setupApp` 在安装路由前完成 `GET /api/auth/session`；异步路由守卫等待该初始化完成。401 是进入登录页的唯一认证失败信号；网络错误保留明确的不可用状态/重试，不得伪装成用户已登出。

### 4. 统一服务端当前用户依赖，删除 Bearer 路径

以 Session 依赖替换 `backend/services/user_service.py` 中 JWT/Bearer 解析：读取 Cookie，哈希查找有效且未撤销的 Session，加载用户并设置 `request.state.auth_user`。所有既有受保护 API 继续声明 `CurrentUser` 依赖，因此业务 API 与 Service 的用户隔离逻辑不变。删除 JWT 密钥、JWT 配置、OAuth2PasswordBearer、`Authorization` 注入、`X-Refresh-Token` 和滑动 JWT 中间件。

### 5. 双提交 CSRF Token，兼容 SSE 停止 Beacon

成功登录和 `GET /api/auth/session` 返回随机 CSRF Token；Token 仅存浏览器内存且与 Session 绑定/可轮换。`authFetch` 对所有非安全方法附加 `X-CSRF-Token`；服务端在执行业务逻辑前验证该 Header。页面卸载时 `navigator.sendBeacon` 不能可靠附加 Header，因此 `/api/chat/sessions/{session_id}/stop` 接受 JSON body 的 `csrf_token` 作为同等校验来源，前端将内存 Token 写入 Beacon payload。安全方法不要求 CSRF Token。

认证中间件/依赖先解析 Session，再由 CSRF 依赖验证非安全请求；缺失或无效 Token 返回 403 统一失败响应，绝不将其处理为 401 或主动清除会话。SSE 的 stream/stop API 仍使用 POST，必须携带 CSRF Token；SSE 连接断开仍走既有 partial 持久化路径，而不依赖 Cookie 续期 Header。

## Risks / Trade-offs

- [Session 表在每个受保护请求读取会增加数据库负载] → 为查询建立 Session 摘要与有效性索引；续期写入按窗口节流，后续仅在性能数据证明需要时增加非权威缓存。
- [Cookie 鉴权会产生 CSRF 风险] → 所有状态变更强制验证会话绑定 CSRF Token，生产 Cookie 使用 Secure/SameSite，部署只允许受控 Origin。
- [移动端无网络时无法确认会话] → 前端显示可重试的网络不可用状态，不删除内存用户资料或误导用户重新登录。
- [部署在跨站前后端域名时 Cookie 规则更严格] → 生产部署保持同源反向代理；若未来明确支持跨站，另行设计 `SameSite=None`、严格 Origin/CORS 与 CSRF 策略。
- [数据库泄露 Session 摘要] → 原始 Session ID 只在 Cookie 存在，服务端使用常量时间比较适用处并可撤销全部会话。

## Migration Plan

1. 添加 Session 数据模型、索引、配置和服务层测试。
2. 实现 `/api/auth`、Cookie、Session 当前用户依赖与 CSRF 防护，并将全部现有受保护接口切换到该依赖。
3. 改造前端启动、路由、请求包装、登录和聊天 stop Beacon，移除 Token 存储与 Bearer Header。
4. 在测试环境验证登录、后台页面重建恢复、空闲/绝对过期、单会话撤销、全部撤销、CSRF 拒绝和 SSE 停止/断连落库。
5. 以破坏性发布部署；回滚仅限恢复前一版本应用与数据库迁移回滚。由于无存量用户，不提供跨版本会话迁移或双轨鉴权。

## Open Questions

- 空闲期限、绝对期限和 Session 续期写入窗口的最终数值应在部署配置中确定；本设计只规定其相对语义。
- 设备列表向用户展示的 IP 精度和 User-Agent 解析库需在实现时按隐私与运维要求确定。
