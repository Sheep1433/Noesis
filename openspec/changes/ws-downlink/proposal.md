## Why

实测（2026-08-26）：对话进行中新开浏览器窗口，设置页等所有 API 请求排队卡死。`lsof` 显示浏览器到前端恰好 **6 条 ESTABLISHED = HTTP/1.1 每源连接上限**。

根因是三条**常驻**信令流各占一个连接池名额，每个聊天窗口固定消耗 3 个：

| # | 端点 | 用途 | 生命周期 |
|---|------|------|---------|
| 1 | `GET /api/chat/events/stream` | 用户信令（会话列表 run 状态 patch） | 页面常驻 |
| 2 | `GET /api/chat/sessions/{sid}/events` | 会话信令（跨窗口发现活跃 run） | 当前会话常驻 |
| 3 | `GET /api/chat/sessions/{sid}/children/stream` | 子 Agent / 后台任务目录 | 当前会话常驻 |

双窗口 3+3=6 即打满；再叠加瞬态的 run SSE（对话流式期间）后，一切普通请求在浏览器排队。HTTP/1.1 的 6 连接限制是浏览器协议层约束，站点不可配置。

DeepSeek Harness（dsh）在其设计记录（`.agents/notes/implemented/architecture/2026-08-04-websocket-downlink-carrier.md`）中记录了**完全相同的问题与解法**：常驻 SSE 下行改用 WebSocket——WS 完成协议升级后脱离浏览器 HTTP 连接池，长连接不再消耗 6 个名额；HTTP 上行（普通请求）不变。dsh 同时否决了「依赖 HTTP/2」（本地 dev/preview 是明文 HTTP/1.1，前置代理不是产品不变量——与 Noesis 本地验证场景一致）与「合并为单连接多路复用」（增加 channel 标签/背压/就绪语义复杂度，收益不成立）。

## What Changes

- 三条常驻信令流（上表 #1/#2/#3）从 SSE 迁移到 **WebSocket 只下行**通道；协议升级后即释放 HTTP 连接池名额。
- run 流式对话（`/runs/{run_id}/stream`）与子 run 事件流**保留 SSE 不动**——瞬态、run 结束即释放，且是 HTTP 响应流式输出的自然形态。
- 删除三条信令流的旧 SSE 端点，**不保留 SSE 回退**（参照 dsh：双物理载体因代理/握手差异静默分叉，问题会留在受支持分支里）。
- 前端 `EventSource`/fetch-流客户端替换为带重连的 WS 客户端；vite 代理为 WS 路径单开 `ws: true` 转发（避开现有 `/api` 代理 `ws: false` 的历史坑）。

## Impact

- 后端：FastAPI WebSocket 端点 ×3（复用现有 `user_signal_bus` / `session_signal_bus` / `AgentCatalogService` 三个总线，订阅、快照首帧、订阅上限语义原样保留）；`websockets` 库已在依赖中。
- 前端：新增 WS 客户端（重连退避 + 会话切换时关闭/重开 session 级连接）；`userSignalStream` / `childCatalogStream` / `useSSEStream.pumpSessionSignals` 三处替换。
- 部署：vite dev/preview 代理加 `/api/ws` 前缀条目（`ws: true`）；生产 nginx 需加 `Upgrade`/`Connection` 透传头（compose 部署涉及）。
- 认证：同源 cookie 在 WS 握手自动携带；握手校验 `Origin` 同源（防跨站 WS 劫持），语义与现 SSE 等价。
- 无数据库变更；优先级低，暂不实施。
