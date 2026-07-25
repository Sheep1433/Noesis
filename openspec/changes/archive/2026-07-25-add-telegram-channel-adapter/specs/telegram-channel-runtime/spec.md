## ADDED Requirements

### Requirement: Telegram 运行时 SHALL 在开关启用时 long-poll 入站

当配置 `messaging.telegram_runtime_enabled=true` 时，系统 SHALL 对每个用户已启用且含 bot token 的 `type=telegram` 通道启动 long-poll（`getUpdates`）。开关关闭时 **SHALL NOT** 发起 Bot API 轮询。

#### Scenario: 开关关闭不轮询

- **WHEN** `telegram_runtime_enabled=false`
- **THEN** 系统 SHALL 不调用 Telegram `getUpdates`

### Requirement: 入站消息 SHALL 经配对后写入 SSOT 并 headless 跑 Agent

已配对入站文本 SHALL：解析 ChannelBinding → `get_or_create_session` → 写入 user 消息（含 `origin=telegram` 与外部 message id）→ 无浏览器 SSE 的 headless Agent 跑次 → PersistSink 终态落库。未配对 SHALL 拒绝跑 Agent，并可回复配对引导。

#### Scenario: 未配对拒绝

- **WHEN** 未配对 chat 向 bot 发消息且运行时已启用
- **THEN** 系统 SHALL NOT 执行该用户特权 Agent，MAY 发送配对提示

#### Scenario: 已配对跑通

- **WHEN** 配对 chat 发送文本且运行时启用
- **THEN** 对应 session 的 messages SHALL 含该 user 消息与终态 assistant 行

### Requirement: 出站 SHALL 支持伪流式文本与工具进度且不依赖浏览器 SSE

系统 SHALL 经 `sendMessage` + 节流 `editMessageText` 向该 chat 投影 assistant 文本（可带光标预览，终态去掉光标）。工具开始时 SHALL 使用**独立**进度消息展示工具名/短 preview（可 accumulate 更新）；**SHALL NOT** 将完整 tool output 发到 Telegram。**SHALL NOT** 要求存在浏览器 SSE 连接。

#### Scenario: 无网页在线仍投递

- **WHEN** headless run 成功完成且无 SSE 订阅者
- **THEN** 用户仍 SHALL 在 Telegram 收到回复（含流式过程中的 edit 与终态）

#### Scenario: 工具进度不镜像结果

- **WHEN** Agent 调用工具并返回大段 output
- **THEN** Telegram 进度气泡 SHALL 仅含工具名与短 preview，SHALL NOT 含完整 tool output

### Requirement: Telegram HITL 审批 SHALL 对齐网页 approve/reject

当 headless run 以 `hitl_pending` 结束且 kind 为审批时，系统 SHALL 向该 chat 发送含工具摘要的审批卡片，并附 Inline Keyboard（批准 / 拒绝；网络类 execute 可附「本会话放行」）。用户点击 SHALL 调用与网页相同的 decisions / grant_scope resume 路径继续同一 `assistant_message_id`，**SHALL NOT** 要求打开浏览器。clarification（ask_user）SHALL 接受下一条文字消息作为 respond。

#### Scenario: 批准后继续

- **WHEN** 用户点击「批准」且 pending HITL 仍有效
- **THEN** 系统 SHALL resume SuperAgent 并继续向 Telegram 投影后续输出

#### Scenario: 拒绝

- **WHEN** 用户点击「拒绝」
- **THEN** 系统 SHALL 以 reject decision resume，并移除键盘
