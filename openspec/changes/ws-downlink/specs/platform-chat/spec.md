## MODIFIED Requirements

### Requirement: 流式问答与 SSE 核心契约

浏览器实时响应 SHALL 使用 `/api/chat` 下的 run 创建与 SSE 订阅端点。系统 SHALL 提供独立的 run 创建、状态查询、SSE 订阅和停止能力，并 SHALL 删除 `POST /api/chat/sessions/stream`。run 事件流 SHALL 保持 SSE；常驻信令流（用户信令、会话信令、子 Agent 目录）SHALL 使用 WebSocket 下行（见「常驻信令流 SHALL 使用 WebSocket 下行」），SHALL NOT 以 SSE 形态保留。

事件类型至少覆盖：`run-snapshot`、`run-status`、`reasoning-*`、`text-*`、`tool-call-*` / `tool-input-*`、`tool-output-available`、`context-update`、`hitl-required`、`error`、`finish`、`[DONE]`。业务事件 SHALL 携带 `run_id` 与 sequence；keepalive 注释帧 SHALL 仅由传输层注入。

#### Scenario: 创建后独立订阅
- **WHEN** 已认证用户成功创建 run
- **THEN** 创建响应 SHALL 返回 `run_id` 与 `assistant_message_id`
- **AND** 客户端 SHALL 能使用独立 SSE 端点订阅该 run

#### Scenario: 文本增量兼容
- **WHEN** run 产生文本增量且客户端订阅 SSE
- **THEN** 客户端 SHALL 收到兼容的 `text-delta` 帧

## ADDED Requirements

### Requirement: 常驻信令流 SHALL 使用 WebSocket 下行

用户信令、会话信令与子 Agent 目录三类常驻流 SHALL 经 WebSocket 只下行通道推送（协议升级后不占用浏览器 HTTP/1.1 连接池名额）；客户端 SHALL NOT 在这些连接上发送业务数据，订阅范围 SHALL 由 URL 路径携带。连接建立 SHALL 先下发快照首帧；保活 SHALL 使用 WebSocket 协议层 ping/pong。旧 SSE 端点 SHALL 删除，系统 SHALL NOT 保留 SSE 回退双载体。WebSocket 握手 SHALL 校验同源 cookie 会话与同源 `Origin`（跨源握手 SHALL 拒绝）；订阅上限 SHALL 沿用现有总线限制语义。run 对话流与子 run 事件流 SHALL 保持 SSE 不变。

#### Scenario: 双窗口不耗尽连接池
- **WHEN** 两个浏览器窗口同时打开聊天页且其中一窗口正在流式对话
- **THEN** 浏览器对前端的 HTTP 连接数 SHALL 保持在连接池上限内
- **AND** 常驻信令经 WebSocket 送达，普通 API 请求 SHALL 即时响应

#### Scenario: 快照首帧对齐
- **WHEN** WebSocket 连接建立
- **THEN** 服务端 SHALL 先下发当前快照（活跃 run / 当前目录）
- **AND** 断线重连后 SHALL 重新对齐快照

#### Scenario: 跨源握手拒绝
- **WHEN** 非同源 Origin 的 WebSocket 握手请求到达
- **THEN** 系统 SHALL 拒绝该握手

#### Scenario: 会话切换重开连接
- **WHEN** 用户切换会话
- **THEN** 客户端 SHALL 关闭旧会话级连接并建立新连接
- **AND** 旧连接的迟到事件 SHALL 被代际守卫丢弃
