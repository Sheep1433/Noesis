## 1. 接缝与模块边界

- [ ] 1.1 在现有 `agent/` + `qa_service` 上约定轻量 run 编排接缝（如 `RunOrchestrator.start_run` / `cancel`）：组 sinks、启动现有 Agent 流；**不**依赖 `extract-agent-runtime-harness` / `noesis_runtime` 搬家
- [ ] 1.2 约定模块目录（如 `domain/chat/delivery/` 或 `services/run_delivery/`）：Bus、PersistSink、SseCodec、ChannelRegistry 边界与 import 方向

## 2. RunEvent 与总线

- [ ] 2.1 定义 RunEvent 类型（含 BusinessEvent 扩展点）与 `run_id` / `assistant_message_id` 关联
- [ ] 2.2 实现 RunEventBus（多订阅）；单测：双 sink 均收到完成事件；heartbeat 不进 bus
- [ ] 2.3 从 `LangGraphSseBridge` 拆出 `LcEventMapper`（LC/`__tw_*` → RunEvent）

## 3. PersistSink 与生命周期

- [ ] 3.1 实现 PersistSink：骨架 INSERT、终态 UPDATE、origin 元数据；删除无效 persist_tick 空路径
- [ ] 3.2 统一 RunLifecycle.cancel（user_stop / disconnect / channel_stop）；对齐现网 `/stop` 与断连 partial 语义
- [ ] 3.3 回归测试：无 SseDelivery 时仍终态落库；stop 与断连互斥不双写

## 4. SseDelivery（契约冻结）

- [ ] 4.1 实现 SseCodec：RunEvent → 现网兼容 SSE 帧；快照/契约测试对照关键事件
- [ ] 4.2 `POST /api/chat/sessions/stream` 改为注册 PersistSink + SseDelivery；keepalive 仅在 SseDelivery
- [ ] 4.3 前端 `useSSEStream` 冒烟：无需改事件分支即可完成一轮对话；`uv run app.py` 可启动

## 5. Channel SPI 与绑定

- [ ] 5.1 定义 ChannelAdapter Protocol、ChannelCapabilities、ChannelRegistry
- [ ] 5.2 ChannelBinding 存储与解析 API（对接 `add-agent-user-settings` 数据模型）
- [ ] 5.3 入站：InboundMessage → 写 user 消息 SSOT → start_run；未配对拒绝测试
- [ ] 5.4 注册 `telegram` / `wechat` stub adapter（capabilities 不同）；出站投影单测（final-only vs throttled-edit 接口）

## 6. 收敛旧路径与文档

- [ ] 6.1 删除或隔离旧「generator 内落库+成帧」双路径；合并 `exec_test_case_resume` 到同一 Fan-out 模板（若成本可控）
- [ ] 6.2 更新 `backend/AGENTS.md` / 流式说明：RunEvent Fan-out 图；注明 WS 非 P0；注明 harness 搬家已搁置
- [ ] 6.3 在 `docs/NOTES.md` 追加知识卡片（架构变更）
- [ ] 6.4 全量相关 pytest：streaming / stop / disconnect / tool events
