# 通讯通道

> 状态：Current
> OpenSpec：`agent-delivery`、`add-feishu-channel-adapter`

## 范围

Noesis 通过统一 ChannelAdapter 将网页之外的消息接入同一次 Agent run。目前真实运行时包括 Telegram Bot API long-poll 与飞书企业自建应用 WebSocket；微信仍为架构测试 Stub，不具备真实收发能力。

通道入站写入与网页相同的消息 SSOT，assistant 终态由 PersistSink 落库。浏览器 SSE、平台发送成功与否都不是落库前提；平台投递失败写独立 delivery 结果。

## 飞书配置

1. 在飞书开放平台创建企业自建应用并启用机器人。
2. 申请机器人读取单聊消息、读取群内 @机器人消息、发送消息和卡片交互所需权限。
3. 在事件订阅中选择长连接，订阅 `im.message.receive_v1` 与卡片回调，并发布应用版本。
4. 部署方通过 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 配置一个共享应用，然后开启 `messaging.feishu_runtime_enabled`。
5. 每个 Noesis 用户在设置页填写自己的飞书 Open ID；需要测试主动投递时再填写 Chat ID。

App Secret 只从部署环境读取，不进入用户通道存储、HTTP、审计或导出。同一飞书租户内的不同用户共用一个机器人连接，入站按发送者 Open ID 路由到各自的 Noesis 用户、session 和 Agent 配置。

## 数据流

```text
Feishu WebSocket event
  → FeishuChannelAdapter normalize / dedupe
  → sender open_id binding + group @ policy
  → ChannelRunService
  → RunEvent / PersistSink
  → FeishuOutbound reply / throttled update / final fallback
```

SDK handler 只负责快速确认并把事件送回主 asyncio loop，禁止在回调内等待 LLM 或工具。幂等优先使用 `event_id`，缺失时使用 `message_id`。

## 安全边界

- 飞书授权主体是发送者 `open_id`，不是群 `chat_id`；同群其他成员不会继承配对权限。
- 群聊只有明确 @机器人时触发 Agent。
- 工具进度只展示名称和短状态，不发送完整参数、output 或 secret。
- HITL 卡片只携带短期不透明 token；resume 时校验用户、session、pending 与过期状态。

## 运行边界与排障

当前使用 `lark-oapi` 官方 SDK 的阻塞式 WebSocket client，由 daemon thread 承载。单个 Noesis 进程建立一个共享应用连接，可服务该应用可见范围内的多个已绑定用户。不同飞书租户或不同应用需拆分 Noesis 实例；多副本部署只允许一个实例开启飞书 runtime，后续由 distributed channel lease change 解决选主。

- `unknown`：运行时未启动或已停止。
- `unavailable`：凭据、权限、网络或 WebSocket 建连失败。
- `degraded`：通道可用但部分能力受限。
- 测试连接调用机器人信息接口；测试消息要求配置目标 Chat ID。

关闭 `messaging.feishu_runtime_enabled` 可停止处理飞书入站，不影响已保存配置、网页聊天或 Telegram。
