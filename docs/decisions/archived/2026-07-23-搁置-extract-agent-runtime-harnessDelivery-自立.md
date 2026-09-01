# 决策：搁置 extract-agent-runtime-harness，Delivery 自立

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 整包迁 `noesis_runtime/` + Profile 注册表 + Harbor 全切影响面过大，短期不解锁多通道；真正需要的是 RunEvent Fan-out 与落库/SSE 解耦。

**How to apply：**
- 主线改为 `unify-run-delivery`（在现有 `agent/` + `qa_service` 上抽 Bus / PersistSink / SseDelivery / ChannelAdapter），**不**等待 harness。
- `extract-agent-runtime-harness` 标 SUPERSEDED 并 archive（`--skip-specs`）；远期若需要可另开 slim「仅 AgentRunService / 评测同入口」。
- `add-agent-user-settings` 仍只做配置面；真收发跟 Delivery。
