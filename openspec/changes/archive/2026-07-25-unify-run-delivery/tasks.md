## 1. 接缝与模块边界

- [x] 1.1 在现有 `agent/` + `qa_service` 上约定轻量 run 编排接缝（如 `RunOrchestrator.start_run` / `cancel`）：组 sinks、启动现有 Agent 流；**不**依赖 `extract-agent-runtime-harness` / `noesis_runtime` 搬家
- [x] 1.2 约定模块目录（如 `domain/chat/delivery/` 或 `services/run_delivery/`）：Bus、PersistSink、SseCodec、ChannelRegistry 边界与 import 方向

## 2. RunEvent 与总线

- [x] 2.1 定义 RunEvent 类型（含 BusinessEvent、**HitlRequired / RunPaused(hitl_pending)**）与 `run_id` / `assistant_message_id` 关联
- [x] 2.2 实现 RunEventBus（多订阅）；单测：双 sink 均收到完成事件；heartbeat 不进 bus
- [x] 2.3 从 `LangGraphSseBridge` 拆出 `LcEventMapper`（LC/`__tw_*`/interrupt → RunEvent，含 HITL）

## 3. PersistSink 与生命周期

- [x] 3.1 实现 PersistSink：骨架 INSERT、终态 UPDATE、origin 元数据；删除无效 persist_tick 空路径
- [x] 3.2 统一 RunLifecycle.cancel（user_stop / disconnect / channel_stop）；对齐现网 `/stop` 与断连 partial 语义
- [x] 3.3 HITL：`HitlRequired`/`hitl_pending` **不**终态；parts pending；`hitl/resume` 同 Fan-out 续写同一 `message_id`；超时 reject 后再终态
- [x] 3.4 回归测试：无 SseDelivery 时仍终态落库；stop 与断连互斥不双写；HITL pending 不 completed

## 4. SseDelivery（契约冻结）

- [x] 4.1 实现 SseCodec：RunEvent → 现网兼容 SSE 帧（含 `hitl-required` / `hitl_pending`）；快照/契约测试对照关键事件
- [x] 4.2 `POST /api/chat/sessions/stream` 与 `hitl/resume` 改为注册 PersistSink + SseDelivery；keepalive 仅在 SseDelivery
- [x] 4.3 前端 `useSSEStream` 冒烟：无需改事件分支即可完成一轮对话与 HITL resume；`uv run app.py` 可启动

## 5. Channel SPI 与绑定

- [x] 5.1 定义 ChannelAdapter Protocol、ChannelCapabilities、ChannelRegistry
- [x] 5.2 ChannelBinding 存储与解析 API（对接 `add-agent-user-settings` 数据模型）
- [x] 5.3 入站：InboundMessage → 写 user 消息 SSOT → start_run；未配对拒绝测试
- [x] 5.4 注册 `telegram` / `wechat` stub adapter（capabilities 不同）；出站投影单测（final-only vs throttled-edit 接口）

## 6. 收敛旧路径与文档

- [x] 6.1 删除或隔离旧「generator 内落库+成帧」双路径；合并 `exec_hitl_resume` / `exec_test_case_resume` 到同一 Fan-out 模板（若成本可控）
- [x] 6.2 更新 `backend/AGENTS.md` / 流式说明：RunEvent Fan-out 图；注明 WS 非 P0；注明 harness 搬家已搁置；注明 channels **配置 vs 运行时** 分属 settings / Delivery
- [x] 6.3 在 `docs/NOTES.md` 追加知识卡片（架构变更）
- [x] 6.4 全量相关 pytest：streaming / stop / disconnect / tool events / hitl_pending
