# 决策：unify-run-delivery：RunEvent Fan-out 落地

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 浏览器 SSE 与未来 TG/微信共用同一 run；落库不能绑在「有没有 SSE 客户端」上；HITL pending 不能被当成 completed。

**How to apply：**
- 内部事件：`domain/chat/delivery/`（`RunEvent` / `RunEventBus` / `LcEventMapper` / `SseDelivery` / `PersistSink` / `RunOrchestrator` / ChannelAdapter SPI）。
- `qa_service` 流式路径经 Orchestrator：raw → Mapper → Bus → SseDelivery；**keepalive 只在 SseDelivery**。
- PersistSink：`HitlRequired` / `RunPaused(hitl_pending)` **不**终态；resume 仍同 `message_id`。
- channels：**配置面**在 settings；Delivery 只跑 Adapter/Binding/入站拒绝未配对。
- `LangGraphSseBridge` 暂作 Mapper 实现细节，对外 SSE 契约冻结；WS / harness 搬家非本 change。
