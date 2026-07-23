## Context

Noesis 流式路径将 Agent 执行、SSE 成帧、消息落库绑在同一 generator（`QaService.exec_query` + `LangGraphSseBridge` + 单消费者 `MemoryStreamBridge`）。`add-agent-user-settings` 提供通道凭据与绑定配置面。

原计划依赖的 `extract-agent-runtime-harness`（整包迁入 `noesis_runtime/`、Profile 注册表、Harbor 全切）**影响面过大、ROI 靠后**，已搁置并由本 change **supersede** 其「事件与投递解耦」目标。本 change 在 **现有** `backend/agent/` + `qa_service` 上完成 Delivery 层：typed 事件、Fan-out、ChannelAdapter SPI；浏览器 SSE **对外契约冻结**。

参考：Hermes Gateway（SessionStore + delivery adapters）、Clowder（消息 SSOT + 网页 live / IM projection 分离）。

## Goals / Non-Goals

**Goals:**

- RunEvent 为内部唯一语言；多 sink 订阅同一次 run。
- PersistSink 独占落库状态机；与传输生命周期解耦。
- SseDelivery 仅为浏览器投递；成帧与 LC 映射分离。
- 轻量 run 编排接缝（组 sinks / start / cancel），不依赖先搬家。
- ChannelAdapter SPI + ChannelBinding；微信/飞书/Telegram 同模型扩展。
- 清理落库 tick 空操作与 stop/断连互斥碎片（收敛到 RunLifecycle）。

**Non-Goals:**

- **BREAKING** 变更对外 SSE 事件名或 JSON 字段（契约冻结）。
- 用 WebSocket 替换 SSE（SessionHub WS 可选后续）。
- 整包 `noesis_runtime/` 物理拆分、Agent Profile 注册表、Harbor 强制同入口（远期可选 slim change）。
- 完整实现微信/Telegram 生产收发（adapter 契约 + 至少一条 stub/参考实现即可）。
- 设置页、cron 配置 UI（其他 change）。
- 改变 assistant 骨架—终态语义（仍 authoritative 服务端落库）。

## Decisions

### D1：自立边界（不依赖 harness 搬家）

```
backend/agent/*（保留）
  astream_events / Agent.run
         │
LcEventMapper → RunEvent
         │
RunEventBus（多订阅）
         │
┌────────┼────────┬─────────────────┐
│        │        │                 │
PersistSink  SseDelivery  ChannelDelivery…
（落库）    （浏览器）   （TG/微信 stub…）
         │
QaService / 轻量 RunOrchestrator
  注册 sinks → 启动现有 Agent 流 → RunLifecycle.cancel
```

- **SHALL NOT** 以 `extract-agent-runtime-harness` 合入为前提。
- 允许日后把 `RunOrchestrator` 再抽成独立 `AgentRunService` 或迁包，但本 change **不**做目录大搬家。
- 同一时刻 **SHALL** 只有一条「发布 RunEvent」的生产路径（禁止旧 generator 落库与新 Fan-out 长期双轨）。

### D2：RunEvent 模型（示意）

```python
# 逻辑事件（非 SSE 字符串）
RunStarted(run_id, session_id, assistant_message_id, origin)
TextDelta / TextEnd / ReasoningDelta / ...
ToolInputStart / ToolInputAvailable / ToolOutputAvailable
UsageUpdate / ContextUpdate
BusinessEvent(type=..., payload=...)  # test-case phase 等
HitlRequired(...)  # 对齐现网 hitl-required 载荷
RunPaused(reason=hitl_pending)  # 本段传输可结束；run 未终态
RunCompleted / RunAborted / RunError
```

`LcEventMapper`（自现 `LangGraphSseBridge` 拆出）负责 LC `astream_events` + `__tw_*` + interrupt → RunEvent（含 `HitlRequired`）。  
`SseCodec` 负责 RunEvent → 既有 `event:` / `data:` 帧（**比特级兼容**现网前端，含 `hitl-required` / `finish_reason=hitl_pending`）。

**HITL 语义（与主规格 `platform-chat` 一致，Fan-out 必须保留）：**

- interrupt → 发布 `HitlRequired` + `RunPaused(hitl_pending)`；SseDelivery 可对本段 HTTP 流发 `finish` + `[DONE]`。
- PersistSink **SHALL NOT** 将此时视为 `completed` / `partial` / `error` 终态；assistant **保持** `status=streaming`，parts 记 pending HITL。
- `hitl/resume`（及 test-case resume）**SHALL** 复用同一 Fan-out：新开 SseDelivery 段、续写同一 `assistant_message_id`，直至真正 `RunCompleted` / abort / error。
- HITL 超时 reject 后再走终态落库（可无活跃 SSE）。

### D3：RunEventBus

- 每 `run_id` 一个多消费者广播（进程内 asyncio；后续可换 Redis，接口稳定）。
- 替换「单消费者 Queue = 业务流」模型；keepalive **仅**由 SseDelivery 在无事件空闲时注入注释帧，**不**进入 RunEvent 总线（避免 IM 收到假心跳事件）。

### D4：PersistSink

- 订阅 RunEvent；拥有骨架 INSERT、终态 UPDATE、stop/断连 partial。
- `origin`：`web` | `telegram` | `wechat` | `cron` | `eval` 写入 message/session extra，便于审计。
- Checkpoint：仅保留有意义的 session context 合并；删除无效的 persist_tick 空路径。
- **SHALL NOT** 因「无 SSE 订阅者」而跳过终态落库。
- **HITL：** 收到 `HitlRequired` / `RunPaused(hitl_pending)` 时 **SHALL** 更新 parts（pending），**SHALL NOT** 终态；仅在真正 `RunCompleted` / `RunAborted` / `RunError`（含超时 reject 后）终态。resume 段 **SHALL** 续写同一 `assistant_message_id`。

### D5：SseDelivery

- HTTP `StreamingResponse` 订阅 bus → `SseCodec` → yield bytes。
- 客户端断开 → `RunLifecycle.notify_disconnect`：PersistSink 按现语义 partial；**不**必然 cancel Agent（可配置；默认与现网「断连 partial、尽量停跑」对齐，在 tasks 中回归）。
- `/api/chat/sessions/stream` 路径与鉴权不变。

### D6：ChannelAdapter SPI

```text
class ChannelAdapter(Protocol):
    channel_type: str  # telegram | wechat | feishu | ...
    capabilities: ChannelCapabilities  # streaming_edit, max_len, markdown, ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    # inbound: platform → InboundMessage → SessionRouter → start_run
    # outbound: 订阅 RunEvent，按 capabilities 投影（FinalTextOnly vs ThrottledEdit）
```

微信等差异关在 adapter（API 形态、加密、客服消息窗口），编排层 **不** if-else 平台细节。

出站默认策略：

| capabilities.streaming_edit | 行为 |
|-----------------------------|------|
| true（如 TG） | 占位 + 节流 edit + 终态 |
| false（多数微信场景） | 仅 RunCompleted 后投递终态文本（可分段） |

工具/推理细节默认 **不** 镜像到 IM，除非 adapter 显式开启。

### D7：ChannelBinding 与入站（运行时；配置面另见 settings）

```text
(user_id, channel_type, external_chat_id[, thread_id]) → session_id
```

**职责边界（相对 `add-agent-user-settings`）：**

| `add-agent-user-settings` | 本 change（Delivery） |
|---------------------------|----------------------|
| 通道 CRUD、密钥、配对/绑定**存储**、设置 UI | 读取绑定；ChannelAdapter 入站/出站；RunEvent Fan-out |
| **不**实现 webhook/long-poll 投递管线 | SPI + stub；生产 TG/微信 adapter 可另 change |

- 配对/绑定数据来自 settings 存储；Delivery **SHALL NOT** 另起一套用户可写「通道密钥」源。
- 未配对入站 **拒绝** 特权执行（可提示配对）。
- 入站用户消息 **SHALL** 写入同一 messages SSOT（`extra.channel` / `external_message_id`）。

### D8：与 SSE 契约冻结

- 前端 `useSSEStream` **无需**为 P0 修改事件分支。
- 文档中过时事件名（如 `token-details`）可另开文档清理，**不**借机改线上帧名。
- 允许新增 **可选** 事件仅当双端协商；本 change 默认不新增。

### D9：WebSocket

- **不**作为 P0。
- 预留：`SessionHub` 订阅 `session_id` 级事件，供「TG 发起、网页旁观」。
- 实现前网页可通过刷新 `GET messages` 取得一致性。

### D10：取消与生命周期

统一 `RunLifecycle.cancel(reason: user_stop | disconnect | channel_stop | system)`：

- `user_stop`：对齐现 `/stop` → partial + stopped 文案
- `disconnect`：对齐现断连 partial
- 通道侧 stop 命令映射到同一入口，避免第三套 `_active_streams` 语义

### D11：与已搁置 harness 的关系

| 原 harness 目标 | 本 change | 远期 |
|-----------------|-----------|------|
| Run 事件与投递解耦 | **在此完成** | — |
| 评测与线上同入口 | 非必须 | slim change 可选 |
| `noesis_runtime/` 搬家 | **不做** | 仅当 Delivery 稳定且目录仍痛 |

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| 不搬家导致 qa_service 仍偏厚 | RunOrchestrator + PersistSink 先削薄流式路径；接受 Agent 类暂留 |
| Fan-out 与旧路径双轨 | feature flag 短过渡；tasks 要求删除 generator 内落库 |
| Fan-out 背压 | Persist 同步关键路径；IM 出站可丢中间 delta、保终态 |
| 微信 API 碎片 | SPI + capabilities；具体微信形态另 change |
| 回归 SSE 细微差异 | 契约测试：录制 RunEvent → SseCodec 快照对比现网 |
| 多实例 bus | 首版进程内；多 worker 时 IM inbound 粘滞或 Redis bus（后续） |

## Migration Plan

1. 落地 RunEvent + Mapper + SseCodec，旁路对比（feature flag）后切换 stream 出口。
2. PersistSink 接管落库；删除 qa_service 内重复终态分支。
3. 注册 ChannelAdapter stub；接线 binding 表。
4. 回滚：flag 切回旧 bridge（过渡期保留），再删除旧路径。

## Open Questions

1. HTTP 断连是否始终 cancel Agent，还是允许「仅断 SSE、run 继续 + IM 仍收终态」？（建议：可配置，默认保持现网 cancel/partial）
2. 测试用例 `exec_test_case_resume` 是否同一 Fan-out 模板优先合并？
3. 微信首个 subtype：企业微信应用 vs 公众号客服消息？（实现 change 再定，SPI 先不绑定）
