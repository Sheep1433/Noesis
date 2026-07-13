## 1. Session domain and configuration

- [x] 1.1 定义 Session 空闲期限、绝对期限、续期写入窗口与 Cookie 属性的配置模型，并在开发/生产配置示例中说明同源 HTTPS 部署要求。
- [x] 1.2 新增用户 Session 数据模型、`user_id + session_digest` 与有效会话查询索引，以及对应数据库迁移；原始 Session ID 不得入库。
- [x] 1.3 实现 Session 领域服务：高熵 ID 创建、摘要查询、有效性判定、节流续期、当前/全部/指定会话撤销和安全的设备元数据归一化。
- [x] 1.4 为 Session 服务补充单元测试，覆盖空闲过期、绝对过期、撤销、续期上限、摘要存储及跨设备隔离。

## 2. 后端认证和 CSRF API

- [x] 2.1 新建 `/api/auth` 路由与 API/Service/Scheme 边界，实现登录、当前会话、退出当前会话、退出全部会话、会话列表和指定会话撤销，并使用 `ResponseUtil` 返回统一结构。
- [x] 2.2 用 Session Cookie 当前用户依赖替换 JWT/Bearer 解析，使聊天、知识库、Skill、附件等所有既有受保护接口继续获得 `CurrentUser`。
- [x] 2.3 实现会话绑定的 CSRF Token 签发与验证；对所有受保护非安全方法启用 Header 校验，并为聊天 `/api/chat/sessions/{session_id}/stop` 的 Beacon JSON body `csrf_token` 实现等价校验。
- [x] 2.4 删除 JWT 签发/解码、`Authorization` Bearer、`X-Refresh-Token`、OAuth2PasswordBearer、滑动 JWT 中间件及旧 `/api/user/login`；确保没有受保护路径继续接受旧令牌。
- [ ] 2.5 为认证 API 和全局鉴权补充集成测试，覆盖 Cookie 恢复、401、403 CSRF、当前/全部/指定会话撤销、Cookie 清除和旧 Bearer 拒绝。
- [x] 2.6 在管理员 `t_user` 记录新增全局邀请码摘要与轮换时间；提供仅限服务端执行的 6 位数字邀请码轮换命令。
- [x] 2.7 新增 `POST /api/auth/register`，校验全局邀请码并创建用户和 Cookie Session；无效邀请码或重复用户名不得创建用户或泄露邀请码状态。

## 3. 前端会话恢复与请求安全

- [x] 3.1 重构 `userStore` 为仅保存内存用户资料、初始化状态和内存 CSRF Token，删除 `sessionStorage` Token 读写及 Token 型 getter。
- [x] 3.2 重构 `authHttp`：所有请求使用 Cookie 凭证，非安全请求统一附带 `X-CSRF-Token`，401 仅在会话初始化完成后清理状态并跳转登录，403 CSRF 显示独立错误处理。
- [x] 3.3 修改登录页与前端认证 API，接入 `/api/auth/login` 和 `/api/auth/logout`，不再读取或保存任何认证令牌。
- [x] 3.4 在 `frontend/src/main.ts` 与异步路由守卫中实现应用启动前的 `/api/auth/session` 恢复；网络/5xx 状态提供重试而不跳转登录。
- [x] 3.5 更新聊天流式发送和停止：POST stream/stop 带 CSRF Header，`beforeunload` Beacon body 带 `csrf_token`，并验证既有 stopped、断连 partial、SSE finish UI 行为不变。
- [ ] 3.6 为前端添加或更新测试，覆盖移动端页面重建自动恢复、失效会话跳登录、网络失败不登出、非安全请求 CSRF 注入和 stop Beacon payload。
- [x] 3.7 接入注册页邀请码输入与 `/api/auth/register`；注册成功后恢复内存用户与 CSRF 状态并进入应用。

## 4. 部署、回归与安全验收

- [x] 4.1 更新 `backend/config.example.yaml`、`backend/config.prod.example.yaml`、部署反向代理说明和 README 的认证配置，移除 JWT 变量并加入 Cookie/Session 配置与 HTTPS 要求。
- [x] 4.2 执行后端 `uv run pytest tests/ -q`，重点审查 JWT 删除不会影响 MCP、Qdrant、SSE assistant 持久化、401/403 异常映射和用户数据隔离。
- [x] 4.2a 为邀请码校验、重复用户名和注册签发会话补充后端测试。
- [ ] 4.3 执行前端 `pnpm lint` 与 `pnpm build`，并在移动浏览器实际验证：切后台后页面被回收、返回后有效 Cookie 自动恢复；退出/撤销后无法恢复。
- [ ] 4.4 以全新部署环境进行破坏性发布演练，验证旧 `/api/user/login` 与 Bearer 请求被拒绝、数据库迁移可回滚且无双轨认证残留。
