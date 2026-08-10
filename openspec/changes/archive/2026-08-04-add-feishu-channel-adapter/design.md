## Context

现有 `domain/chat/delivery/channels.py` 已定义 ChannelAdapter SPI，Telegram 在 `domain/chat/delivery/telegram/` 与 `services/channels/telegram_runtime.py` 完成真实收发；`feishu` 目前只被 settings 配置模型接受，没有 registry adapter、运行时或 UI。飞书企业自建应用支持由客户端主动建立 WebSocket 长连接，适合 Noesis 本地和单机部署，也能避免新增匿名公网 webhook。

## Goals / Non-Goals

**Goals:**

- 飞书单聊和群聊 @机器人文本进入同一 `ChannelRunService`、消息 SSOT 与 PersistSink。
- Agent 文本、终态和安全的工具摘要可投递到飞书；投递失败与 run 终态分离。
- HITL approve/reject 使用交互卡片，clarification 可由下一条文本恢复。
- 一个部署级飞书应用由所有已绑定用户共享；用户只管理自己的 Open ID、可选 Chat ID 和路由配置。
- 入站快速确认、异步处理、事件幂等和群聊响应策略可测试。

**Non-Goals:**

- 飞书商店应用、Webhook 入站、跨租户发布。
- 图片、文件、语音、富文本解析、云文档评论和主动通讯录同步。
- 个人微信、公众号、企业微信与钉钉。
- 改写 `/api/chat` SSE、Agent runtime 或消息数据库模型。

## Decisions

### 1. 使用企业自建应用与官方 SDK WebSocket 长连接

`services/channels/feishu_runtime.py` 负责进程生命周期和动态配置 reconcile；官方 SDK 在后台线程维持长连接，事件 handler 只完成校验、去重和投递到 asyncio loop，Agent run 由 loop 内 Task 执行。相比 Webhook，这不需要公网入站、验签路由和反向代理配置；相比自行实现 WebSocket，可直接获得鉴权、心跳与重连。

每个 Noesis 进程使用部署级 `FEISHU_APP_ID` 与 `FEISHU_APP_SECRET` 建立一个客户端，所有用户绑定共享该连接。收到事件后先按 `sender.open_id` 解析 `ChannelBinding`，再读取该 Noesis 用户自己的飞书通道与路由配置；禁止把首个用户配置固定为全局运行配置。handler 必须立即返回，不能等待 Agent。应用级连接异常写入所有已启用飞书通道的 `channel_health`。官方 Python SDK 的阻塞式连接没有稳定的 per-client stop API，因此运行中替换部署凭据需要重启进程。

### 2. 飞书协议封装在 Adapter 子包

新增：

- `domain/chat/delivery/feishu/client.py`：tenant token、发消息、回复消息、更新消息/卡片和凭据脱敏；HTTP 错误转换为稳定异常。
- `adapter.py`：`im.message.receive_v1` 规范化为 `InboundMessage`，提取 `event_id`、`message_id`、`chat_id`、`sender.open_id`、chat_type、mentions 和纯文本。
- `stream_out.py`：先发占位消息，再按节流更新交互卡片或消息；不支持更新时回落到分段终态文本。
- `hitl_prompt.py`：生成 approve/reject 卡片，短 token 仅引用服务端 pending 状态，不在卡片内携带命令或参数。

`ChannelRegistry` 注册真实 `FeishuChannelAdapter`。平台差异不得进入 `RunOrchestrator` 或 SSE bridge。

### 3. 配对键使用 sender open_id，投递目标另存 chat_id

入站授权必须基于 `sender.open_id`，不能仅凭群 `chat_id` 让所有群成员共享授权。`channels.json` 的飞书 pairing 保存 `sender_open_id`，routing 保存或动态记录目标 `chat_id`；固定管理员可在设置页填写两者。群聊只响应明确 @机器人且去除 @文本，单聊直接响应。

现有 `ChannelBinding.external_chat_id` 对飞书承载 `sender.open_id`；`thread_id` 可承载群 `chat_id`。若设置只配置 sender 而没有群目标，入站时在当前 run 上回复原消息，但不得把目标持久化为其他用户可用的授权。

### 4. 事件幂等与并发复用现有通道运行入口

以飞书 `event_id` 为第一幂等键，缺失时使用 `message_id`；进程内 TTL 集合拦截重推，同一外部 message id 还写入 user message extra，依赖 SSOT 唯一语义避免重复 run。`session_strategy=persistent` 沿用默认 session；`new_per_message` 创建新 session。相同 session 的串行约束由现有 run 管理承担。

### 5. 出站与 HITL

飞书声明 `streaming_edit=true`、`markdown=true`、`mirror_tools=false`。文本增量以约 800ms 节流更新，工具只显示工具名和短状态，不发送参数、完整 output、路径或 secret。终态去除光标并按平台限制分段；发送失败只记录 delivery failure。

HITL 卡片 action 携带短 token 和 `approve|reject`，回调映射到现有统一 decision；token 必须校验用户、通道、session、pending 状态和过期时间。需要用户补充信息时，下一条已配对文本映射为 `respond`。回调处理同样先快速确认，再异步 resume。

### 6. 配置与设置 UI

`RuntimeChannelConfig` 对飞书只承载用户级配对和路由，不承载应用凭据。`FEISHU_APP_ID` 与 `FEISHU_APP_SECRET` 只进入服务端部署配置；`/api/user/channels` 的飞书请求和响应均不得包含二者。旧 Telegram Token 的加密存储和读取保持不变。

`ChannelsSection.vue` 增加通道类型选择；根据类型显示 Telegram Token/Chat ID 或飞书 Open ID/可选 Chat ID。`/api/user/channels` 路径、认证与 CSRF 不变。

## Risks / Trade-offs

- [官方 SDK 的长连接为阻塞式且版本 API 变化] → 固定兼容版本，封装在线程边界，增加 fake SDK 生命周期测试。
- [飞书事件可能重推或乱序] → 快速确认、event/message 双层幂等、按 session 串行。
- [群聊 chat_id 不能代表授权用户] → sender open_id 配对，群聊强制 @，授权与回复目标分离。
- [卡片更新限流或格式不兼容] → 节流、长度限制、终态分段 plain-text 回落。
- [多进程会建立重复连接] → 生产多副本只在一个实例开启飞书 runtime；后续再引入分布式 lease。
- [共享应用导致用户路由串线] → 每条入站先以 Open ID 解析绑定，再按绑定 user_id 读取通道配置；增加双用户隔离回归测试。

## Migration Plan

1. 增加 SDK 依赖、配置开关和飞书模块，默认关闭 `messaging.feishu_runtime_enabled`。
2. 在部署环境配置共享应用凭据，扩展 settings schema/API/UI 只管理用户绑定；已有 Telegram 配置无需迁移。
3. 在测试租户创建企业自建应用，开通机器人、消息读取/发送和卡片权限，灰度启用一条通道。
4. 验证单聊、群 @、重推去重、长回复、HITL、断线重连和无浏览器 SSE 后再默认开放设置入口。
5. 回滚时关闭运行时开关并停掉连接；用户绑定保留，删除 Adapter 不影响 Telegram 和网页。

## Open Questions

- 多副本部署的 leader lease 由后续 durable ingress change 统一提供，本 change 只保证单实例正确性。
- 图片、文件和云文档评论等消息类型在独立 change 中扩展，不在首期静默转成文本。
