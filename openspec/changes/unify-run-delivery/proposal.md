## Why

当前 Agent 运行与 HTTP SSE 生命周期、消息落库、单消费者 Queue 强耦合（`qa_service` / `LangGraphSseBridge` 过重），扩展 Telegram/微信等通道只能再抄一条链路。设置页与通道凭据由 `add-agent-user-settings` 覆盖。本 change **自立**建立 **typed RunEvent + 多 Delivery Fan-out**，使浏览器 SSE、IM 通道、Persist 共用同一次 run，且 **对外 SSE 契约冻结**。

**不再依赖** `extract-agent-runtime-harness`（整包 `noesis_runtime` 搬家影响面过大，已搁置/supersede；Delivery 所需接缝在本 change 内于现有 `agent/` + `qa_service` 上抽出）。

## What Changes

- 引入内部 **RunEvent** 模型与 **RunEventBus**（多订阅）；执行产物经 `LcEventMapper` 发布结构化事件，**不**直接 yield SSE 字符串作为权威事件源。
- 将落库抽为 **PersistSink**（骨架—检查点—终态状态机唯一权威），与「是否存在 SSE 客户端」解耦。
- 将现有 SSE 路径降级为 **SseDelivery**（`SseCodec` 成帧）；`LangGraphSseBridge` 职责收缩为 LC→RunEvent 映射 + SSE 投影。
- 在现有代码树上提供轻量 **run 编排接缝**（如 `RunOrchestrator` / `start_run`，可仍由 `QaService` 调用）：组 sinks、启动 Agent 流、统一 cancel——**不**要求先迁 `noesis_runtime/` 包。
- 定义 **ChannelAdapter SPI**（inbound normalize + outbound project + capabilities），首期至少落地契约与 stub 注册表；Telegram/微信等具体 adapter 可分期实现。
- 定义 **ChannelBinding**（外部 chat ↔ Noesis `session_id`）与入站路由到同一 run 编排 + 落库 SSOT。
- **非目标**：整包 Runtime/Harness 物理拆分（已搁置）；全面改 WebSocket 替换 SSE；改变对外 SSE 事件名/载荷（**无 BREAKING**）；实现完整微信/Telegram 生产投递（可跟进）；设置页 UI（属 `add-agent-user-settings`）。

## Capabilities

### New Capabilities

- `agent-run-delivery`：RunEvent 总线、PersistSink / SseDelivery / ChannelDelivery Fan-out、ChannelAdapter SPI、ChannelBinding、与现有 Agent/`QaService` 的轻量 run 接缝；多端消息统一规则。

### Modified Capabilities

- `platform-chat`：流式入口 SHALL 经 Run Fan-out 消费事件；落库由 PersistSink 驱动；对外 SSE 契约保持兼容。
- `agent-messaging-channels`（若尚未归档进主规格，则以本 change delta + `add-agent-user-settings` 为准）：通道运行时投递 SHALL 实现 ChannelAdapter，且 **SHALL NOT** 依赖浏览器 SSE 存活。

## Impact

| 区域 | 影响 |
|------|------|
| `backend/services/qa_service.py` | 变薄：组 sinks + start_run；删除与成帧缠死的落库循环 |
| `backend/domain/chat/streaming/` 或 `delivery/` | 拆 `RunEvent` / bus / PersistSink / SseCodec；瘦身 `langgraph_sse.py` |
| `backend/agent/` | **保留**现路径；Mapper 消费 `astream_events` / Agent 产出；不强制迁包 |
| `add-agent-user-settings` | 通道配置/凭据；本 change 消费 binding 与 adapter 注册 |
| 前端 `useSSEStream.ts` | **无破坏**：事件名与帧格式保持；可选日后旁观 WS |
| API | 仍 `POST /api/chat/sessions/stream`；可新增内部/后续 `WS` 旁观端点（非 P0） |

依赖顺序：**本 change（先行）** → Telegram/微信 adapter →（可选远期）slim `AgentRunService` / 评测同入口 →（更后）物理拆包若仍需要。
