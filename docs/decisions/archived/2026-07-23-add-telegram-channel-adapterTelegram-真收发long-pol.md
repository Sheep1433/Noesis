# 决策：add-telegram-channel-adapter：Telegram 真收发（long-poll）

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 设置页已能存 bot token/配对，Delivery 有 SPI，但无真收发；需要同一套 SuperAgent + SSOT，不依赖浏览器 SSE。

**How to apply：**
- 开关：`messaging.telegram_runtime_enabled`（写在 `config.yaml` / `config.prod.yaml`），**默认 false**。出网走标准 `HTTP(S)_PROXY` / 系统代理，无专用 proxy 配置项。
- Client/Adapter：`domain/chat/delivery/telegram/`；Registry 的 `telegram` 为真 Adapter；出站经 `stream_out` 伪流式。
- Headless：`RunOrchestrator.run_headless` + `services/channel_run_service.run_channel_agent`（`origin=telegram`，PersistSink 落库）。
- Poll：`services/telegram_runtime.py` lifespan 启停；密钥仅 `MessagingChannelService.iter_enabled_runtime`（不经 HTTP）。
- 未配对：拒绝 Agent，回配对 Chat ID 提示。HITL：TG 内仅提示去网页确认。
