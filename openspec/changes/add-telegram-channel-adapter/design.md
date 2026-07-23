## Context

配置面与 Fan-out SPI 已就绪。本 change 落地 Telegram 真收发（P0：long-poll + final-only 出站）。

## Goals / Non-Goals

**Goals:** long-poll 入站 → 配对校验 → SSOT → headless SuperAgent → `sendMessage` 终态文本；feature flag；密钥仅服务端。

**Non-Goals:** 微信；webhook 必选；TG 内完整 HITL UI；工具细节镜像到 IM。

## Decisions

### D1：入站运输

P0 用 **getUpdates long-poll**（无需公网 URL）。每启用 bot 一个 asyncio Task。P1 可加 webhook。

### D2：Headless run

新增 `run_channel_agent`（或 `RunOrchestrator.run_headless`）：复用 `super_agent.run_agent` + PersistSink + skeleton/finalize，`origin=telegram`，不 yield SSE。

### D3：出站

P0：`streaming_edit` 用 placeholder + 节流 `editMessageText`；工具独立进度气泡（accumulate）。P2：`sendMessageDraft`、flood backoff、cleanup_progress。

### D4：配对

`pairing.chat_id` 与入站 `chat.id` 匹配；未配对 `sendMessage` 引导去设置页填写配对。

### D5：密钥

`MessagingChannelService.iter_enabled_runtime("telegram")` 返回含 `bot_token` 的内部结构；禁止 settings HTTP 回传。

## Risks

| Risk | Mitigation |
|------|------------|
| 多 worker 重复 poll | 单实例或按 bot 加锁；文档注明 |
| HITL 在 TG 卡住 | P0 文本提示「请在网页确认」；超时沿用 hitl timeout |
| Token 泄露 | 仅文件+内存；日志脱敏 |

## Migration Plan

1. `telegram_runtime_enabled=false` 默认。
2. 用户设置配对 + token → 开 flag → 重启/热启 supervisor。
3. 回滚：关 flag 停 poll。
