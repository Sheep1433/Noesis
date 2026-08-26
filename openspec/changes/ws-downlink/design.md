# 设计：常驻信令流 WebSocket 下行（ws-downlink）

> 状态：**方案定稿，暂不实施**（低优先级）。本文以现状代码为分析基线。

## 1. 现状与连接数学

浏览器对同一 HTTP/1.1 源的并发连接上限为 6（Chrome/Edge/Firefox/Safari 一致），该值站点不可配置。当前每聊天窗口的连接占用：

```
常驻（页面/会话生命周期内一直占用）：
  1  user-signal    EventSource      /api/chat/events/stream
  1  session-signal fetch 流         /api/chat/sessions/{sid}/events
  1  child-catalog  EventSource      /api/chat/sessions/{sid}/children/stream
瞬态（仅在对应活动期间占用）：
  ~1 run SSE        fetch 流         /api/chat/runs/{run_id}/stream（对话流式期间）
  ~1 子 run 事件流   fetch 流         子 Agent 会话查看期间
```

单窗口对话中 = 4；**双窗口 = 6 打满**；此后任何普通 API 请求（设置页、会话列表、文件读写）在浏览器侧排队，表现为「点什么卡什么」。已实测复现（lsof 恰好 6 条 ESTABLISHED）。

三条常驻流的后端形态一致：认证 → 订阅进程内总线（`user_signal_bus` / `session_signal_bus` / `AgentCatalogService`）→ 连接建立先发快照帧 → 循环推送（15s SSE 注释保活）→ 断开退订；均有每用户/每会话订阅上限（429 + `*_SIGNAL_LIMIT`）。

## 2. 备选方案（含 dsh 的否决记录）

| 方案 | 结论 | 理由 |
|------|------|------|
| **A. 常驻流迁 WebSocket（选定）** | ✅ | WS 升级后脱离 HTTP 连接池，长连接零名额成本；上行（普通请求）不动；协议载体层改动，不触碰业务总线语义。dsh 同形问题已验证此路径 |
| B. 三条合并为一条 SSE | ❌ | 仍永久占 1 个池位（对话流式时双窗口 = 3，虽缓解但未根治）；且需引入 channel 标签分发 + 单连接背压，dsh 以「复杂度不成比例」明确否决 |
| C. 上 HTTP/2 | ❌ | 本地 dev/vite preview 是明文 HTTP/1.1（h2 必须过 TLS），h2 是否可用取决于前置代理——「部署方前面的代理不是产品不变量」（dsh 否决理由原文，同样适用于 Noesis 本地验证场景） |
| D. 全双工 WS（上行也搬） | ❌ | 需重写超时、取消、HTTP 状态码、请求关联语义，对连接槽问题无额外收益（dsh 同样否决；HTTP 上行是刻意保留的边界） |
| E. 保留 SSE 回退（双载体） | ❌ | 代理/握手差异导致生产路径静默分叉，连接数问题留在受支持分支；预发布期单载体、失败显式暴露（dsh 同决策） |

## 3. 设计

### 3.1 端点与帧

三条常驻流各一条 WS，路径独立、生命周期独立（不做跨流多路复用，与现有三条 SSE 语义一一映射，改动最小）：

```
WS /api/chat/ws/user-signals                     ← 替换 GET /api/chat/events/stream
WS /api/chat/ws/sessions/{sid}/signals           ← 替换 GET /api/chat/sessions/{sid}/events
WS /api/chat/ws/sessions/{sid}/children          ← 替换 GET /api/chat/sessions/{sid}/children/stream
```

统一 `/api/ws/` 前缀的目的：vite 代理与 nginx 只对这一前缀开 WS 转发，避开现有 `/api` 代理条目 `ws: false` 的历史坑（注释：开启后大文件 multipart 与 WS 升级冲突）。

**只下行**：客户端不在 WS 上发送任何业务数据（订阅范围由 URL 携带，会话切换 = 关旧开新，与 dsh 一致）。帧格式为现有 SSE 帧的直接映射：

```json
{"event": "user-signal", "data": { ... }}
```

- 连接建立 → 先下发快照帧（沿用现有首帧对齐语义）
- 保活：WS 协议层 ping/pong（服务端 20s ping），替代 SSE 注释帧
- 服务端流错误 → 发一帧 `{"event": "stream-error", ...}` 后关闭 socket；客户端按连接失败处理重连

### 3.2 认证与信任栅栏

- 同源 cookie 在 WS 握手时由浏览器自动携带；服务端用与 `get_current_user` 相同的会话解析（从 cookie 而非 Depends）。
- 握手校验 `Origin`：必须与 Host 同源（防跨站 WS 劫持——浏览器对 WS 握手不带 CSRF token，Origin 是唯一防线；等价于 dsh 的 `/api` 信任栅栏在 WS 上的应用）。
- 订阅上限沿用现有总线限制与 429 语义（超限直接拒绝握手）。

### 3.3 前端客户端

替换三处：

| 现实现 | 改为 |
|--------|------|
| `userSignalStream.ts`（EventSource，内建重连） | WS 客户端 |
| `childCatalogStream.ts`（EventSource，内建重连） | WS 客户端；会话切换时关闭旧连接 |
| `useSSEStream.pumpSessionSignals`（fetch 流 + 自定义退避重连） | WS 客户端（复用其现有 signalSessionId 代际守卫） |

新增 `wsClient.ts`（~80 行）：连接/关闭/指数退避重连（1s→30s 封顶）/代际失效（组件卸载或会话切换后旧连接事件丢弃）/帧解析分发。EventSource 的内建自动重连由此显式承担，断线后的「快照对齐」沿用现有策略（重连即重发全量首帧；user-signal 首连全量刷新列表）。

### 3.4 代理与部署

- **vite（dev + preview）**：新增独立代理条目 `'/api/ws': { target, ws: true }`（vite 按最长前缀匹配，`/api` 条目继续 `ws: false`，互不影响）。
- **nginx/compose**：WS 路径加 `proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";`。
- **uvicorn**：`websockets` 库已在依赖中，无需改动启动参数。

### 3.5 不动的部分（明确边界）

- run 对话流（`/runs/{run_id}/stream`）与子 run 事件流保持 SSE：瞬态、结束即释放、HTTP 状态码/流式语义自然。
- 三个进程内总线（`user_signal_bus` / `session_signal_bus` / `AgentCatalogService`）及其订阅上限、快照逻辑不变——WS 端点只是新的消费载体。
- HITL、投递通道（telegram/feishu）等其他 SSE 面不在本变更范围。

## 4. 验收场景

1. **连接数学**：双窗口 + 对话流式期间，浏览器对前端的 HTTP 连接 ≤ 2（仅 run SSE），WS 连接不占池；设置页请求即时响应。
2. **首帧对齐**：WS 建立即收到快照（活跃 run / 当前目录）；断线重连后重新对齐。
3. **跨源拒绝**：非同源 Origin 的 WS 握手被拒绝。
4. **订阅上限**：超出每用户/每会话订阅上限的握手返回 429 等价语义。
5. **会话切换**：切换会话时旧 session 级 WS 关闭、新连接建立，代际守卫丢弃旧连接迟到事件。
6. **服务重启**：WS 断开后前端退避重连成功，无重复订阅泄漏（服务端退订幂等）。
