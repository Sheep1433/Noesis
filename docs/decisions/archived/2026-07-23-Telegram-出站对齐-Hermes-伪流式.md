# 决策：Telegram 出站对齐 Hermes 伪流式

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** TG 终态一次发送体验差；要对齐 Hermes「主回复 edit + 工具独立进度气泡」。

**How to apply：**
- `domain/chat/delivery/telegram/stream_out.py`：文本 0.8s/24chars + cursor；工具 1.5s accumulate；遇工具先 finalize 文本段。
- `channel_run_service` `on_events` → `TelegramOutbound.feed_events`；`telegram_runtime` 不再二次 send 终态全文。
- 不抄 draft API / cleanup_progress；不镜像 tool output。
