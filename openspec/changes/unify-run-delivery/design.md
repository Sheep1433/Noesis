## Context

Noesis 流式路径将 Agent 执行、SSE 成帧、消息落库绑在同一 generator（`QaService.exec_query` + `LangGraphSseBridge` + 单消费者 `MemoryStreamBridge`）。`extract-agent-runtime-harness` 将引入 `AgentRunService` 与 Runtime/Harness 边界，但明确不做多通道。`add-agent-user-settings` 提供通道凭据与绑定配置面。本 change 在二者之上完成 **Delivery 层**：typed 事件、Fan-out、ChannelAdapter SPI，使 Telegram/微信等可插拔，且浏览器 SSE **对外契约冻结**。

参考：Hermes Gateway（SessionStore + delivery adapters）、Clowder（消息 SSOT + 网页 live / IM projection 分离）、deer-flow runtime/harness。

## Goals / Non-Goals

**Goals:**

- RunEvent 为内部唯一语言；多 sink 订阅同一次 run。
- PersistSink 独占落库状态机；与传输生命周期解耦。
- SseDelivery 仅为浏览器投递；成帧与 LC 映射分离。
- ChannelAdapter SPI + ChannelBinding；微信/飞书/Telegram 同模型扩展。
- 清理落库 tick 空操作与 stop/断连互斥碎片（收敛到 RunLifecycle）。

**Non-Goals:**

- **BREAKING** 变更对外 SSE 事件名或 JSON 字段（契约冻结）。
- 用 WebSocket 替换 SSE（SessionHub WS 可选后续）。
- 完整实现微信/Telegram 生产收发（adapter 契约 + 至少一条 stub/参考实现即可）。
- 设置页、cron 配置 UI（其他 change）。
- 改变 assistant 骨架—终态语义（仍 authoritative 服务端落库）。

## Decisions

### D1：依赖与包边界

```
noesis_runtime (extract-agent-runtime-harness)
    发布 RunEvent
         │
services / domain/chat/delivery/   ← 本 change 新增或重组
    RunEventBus + PersistSink + SseDelivery + ChannelRegistry
         │
api/chat stream 仅挂 SseDelivery（若请求来自 HTTP SSE）
channel workers 挂对应 ChannelAdapter
```

若 harness change 尚未合入：本 change 实现期 **SHALL** 先具备「等价于 AgentRunService 发布事件」的接缝（可暂从现有 bridge 适配），但目标架构以 harness 为准，避免长期双入口。

### D2：RunEvent 模型（示意）

```python
# 逻辑事件（非 SSE 字符串）
RunStarted(run_id, session_id, assistant_message_id, origin)
TextDelta / TextEnd / ReasoningDelta / ...
ToolInputStart / ToolInputAvailable / ToolOutputAvailable
UsageUpdate / ContextUpdate
BusinessEvent(type=..., payload=...)  # test-case phase 等
RunCompleted / RunAborted / RunError
```

`LcEventMapper`（自现 `LangGraphSseBridge` 拆出）负责 LC `astream_events` + `__tw_*` → RunEvent。  
`SseCodec` 负责 RunEvent → 既有 `event:` / `data:` 帧（**比特级兼容**现网前端）。

### D3：RunEventBus

- 每 `run_id` 一个多消费者广播（进程内 asyncio；后续可换 Redis，接口稳定）。
- 替换「单消费者 Queue = 业务流」模型；keepalive **仅**由 SseDelivery 在无事件空闲时注入注释帧，**不**进入 RunEvent 总线（避免 IM 收到假心跳事件）。

### D4：PersistSink

- 订阅 RunEvent；拥有骨架 INSERT、终态 UPDATE、stop/断连 partial。
- `origin`：`web` | `telegram` | `wechat` | `cron` | `eval` 写入 message/session extra，便于审计。
- Checkpoint：仅保留有意义的 session context 合并；删除无效的 persist_tick 空路径。
- **SHALL NOT** 因「无 SSE 订阅者」而跳过终态落库。

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

微信等差异关在 adapter（API 形态、加密、客服消息窗口），Harness **不** if-else 平台细节。

出站默认策略：

| capabilities.streaming_edit | 行为 |
|-----------------------------|------|
| true（如 TG） | 占位 + 节流 edit + 终态 |
| false（多数微信场景） | 仅 RunCompleted 后投递终态文本（可分段） |

工具/推理细节默认 **不** 镜像到 IM，除非 adapter 显式开启。

### D7：ChannelBinding 与入站

```text
(user_id, channel_type, external_chat_id[, thread_id]) → session_id
```

- 配对/绑定数据来自 `add-agent-user-settings` 存储。
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

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| harness 未完成导致双执行入口 | tasks 标明依赖；过渡期单一适配层，禁止第三条路径 |
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
