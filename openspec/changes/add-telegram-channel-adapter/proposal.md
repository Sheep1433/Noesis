## Why

设置页已能保存 Telegram bot token 与配对，Delivery 已有 ChannelAdapter SPI，但尚无真收发。用户需要用 Telegram 对话触发同一套 Agent + 消息 SSOT，且不依赖浏览器 SSE。

## What Changes

- 新增 Telegram 运行时：long-poll `getUpdates`、入站写 SSOT、无 SSE 的 headless Agent 跑次、出站 `sendMessage`（P0 终态文本）。
- 配置开关 `messaging.telegram_runtime_enabled`（默认 false）；读取 settings `channels.json` 密钥（不经 HTTP 回传明文）。
- 未配对 chat 拒绝特权执行并提示配对；启用通道且已配对才跑 Agent。
- **非目标**：微信真收发、webhook 首期必做、完整 HITL 卡片在 TG 内交互（interrupt 可文本提示稍后再 resume）。
- **无 BREAKING** 对外浏览器 SSE。

## Capabilities

### New Capabilities

- `telegram-channel-runtime`：Telegram Adapter、long-poll、入站/出站与 headless run

### Modified Capabilities

- `agent-messaging-channels`：运行时由 stub 升级为可真收发的 telegram adapter（配置面不变）

## Impact

- `backend/domain/chat/delivery/`、`services/messaging_channel_service.py`、新 `services/telegram_*` / `channels/telegram/`
- `server.py` lifespan 启停 polling
- `config/env.py` + `config.yaml` / `config.example.yaml`
- 依赖：现有 `httpx`（异步）
- 依赖已完成 change：`unify-run-delivery`、`add-agent-user-settings`
