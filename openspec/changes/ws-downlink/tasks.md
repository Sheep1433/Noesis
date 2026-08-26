# ws-downlink

> **暂不实施**（低优先级）。实施前置：md-memory-layer 变更合入 dev 后排期。

## 1. 后端 WS 端点

- [ ] 1.1 WS 认证与 Origin 同源栅栏（cookie 会话解析 + Origin 校验，拒绝语义对齐现有 401/403）
- [ ] 1.2 `WS /api/chat/ws/user-signals`：复用 user_signal_bus，快照首帧 + 推送 + 退订；删除 `GET /api/chat/events/stream`
- [ ] 1.3 `WS /api/chat/ws/sessions/{sid}/signals`：复用 session_signal_bus；删除 `GET /api/chat/sessions/{sid}/events`
- [ ] 1.4 `WS /api/chat/ws/sessions/{sid}/children`：复用 AgentCatalogService；删除 `GET /api/chat/sessions/{sid}/children/stream`
- [ ] 1.5 协议层 ping/pong 保活（20s）；stream-error 帧后关闭语义

## 2. 前端

- [ ] 2.1 `wsClient.ts`：连接/退避重连/代际守卫/帧分发（替代 EventSource 内建重连）
- [ ] 2.2 替换 userSignalStream / childCatalogStream / pumpSessionSignals 三处
- [ ] 2.3 重连快照对齐与全量刷新策略回归（首连/重连语义与现 SSE 一致）

## 3. 代理与部署

- [ ] 3.1 vite dev + preview：`/api/ws` 独立代理条目（ws: true；`/api` 保持 ws: false）
- [ ] 3.2 compose/nginx：WS upgrade 头透传
- [ ] 3.3 vite preview 双窗口 + 对话流式的连接数验收（HTTP 连接 ≤ 2）

## 4. 测试与回归

- [ ] 4.1 后端：WS 端点契约测试（认证/Origin/快照/上限/退订幂等/重启重连）
- [ ] 4.2 前端：wsClient 单测（重连退避、代际丢弃、帧分发）；双窗口行为测试
- [ ] 4.3 removal baseline：旧三条 SSE 端点不存在
- [ ] 4.4 全量回归（backend pytest / frontend lint+build+vitest）
