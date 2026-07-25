## 1. 配置与密钥

- [x] 1.1 增加 `messaging.telegram_runtime_enabled`（yaml + env），默认 false
- [x] 1.2 `MessagingChannelService`：`iter_enabled_runtime` / 内部读 `bot_token`（不经 HTTP）

## 2. Headless run

- [x] 2.1 实现 `run_channel_agent`：session + user 消息已写入前提下，SuperAgent headless + PersistSink + 返回终态纯文本
- [x] 2.2 单测：mock agent 流 → 有终态文本；无 SSE

## 3. Telegram Adapter + Poll

- [x] 3.1 `TelegramBotClient`（httpx）：`getUpdates` / `sendMessage`（token 脱敏日志）
- [x] 3.2 `TelegramChannelAdapter`：normalize Update；project_outbound → sendMessage
- [x] 3.3 Long-poll supervisor：按启用通道起 Task；未配对提示；入站编排
- [x] 3.4 `server.py` lifespan start/stop

## 4. 验收

- [x] 4.1 单测：normalize、未配对、出站 final-only / 伪流式节流
- [x] 4.2 `docs/NOTES.md` 追加；`uv run pytest` 相关用例；开关默认关可启动

## 5. Hermes 伪流式（续）

- [x] 5.1 `editMessageText` + `stream_out` 双管道
- [x] 5.2 `channel_run` / `telegram_runtime` 接线
- [x] 5.3 单测 + OpenSpec 出站要求更新

## 6. Telegram HITL（对齐网页审批）

- [x] 6.1 审批卡片 + Inline Keyboard（批准 / 拒绝 / 本会话放行）
- [x] 6.2 `callback_query` → `resume_channel_hitl`；clarification 文字 respond
- [x] 6.3 单测 + OpenSpec HITL 要求
