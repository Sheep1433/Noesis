## Why

Noesis 目前只有 Telegram 可真实收发，国内用户无法在常用协作工具中直接调用 Agent、延续同一会话或完成 HITL。飞书企业自建应用提供官方 WebSocket 长连接和机器人消息能力，可在不暴露公网回调地址的前提下验证第二种真实 ChannelAdapter，并覆盖国内团队协作场景。

## What Changes

- 新增飞书企业自建应用通道：通过官方 WebSocket 长连接接收消息，通过 OpenAPI 回复单聊与群聊。
- 将飞书入站消息映射到统一 `InboundMessage`、ChannelBinding、消息 SSOT 和 headless Agent run；支持固定会话与逐消息新会话策略。
- 按飞书能力投影 Agent 文本、终态和有限工具进度；长回答分段，失败独立记录，不污染 run 终态。
- 用飞书交互卡片承载 HITL approve/reject，并允许后续文本回答 clarification。
- 由部署方配置一个共享飞书应用；扩展 `/api/user/channels` 与设置页通讯区，让不同 Noesis 用户分别绑定自己的飞书 Open ID，并支持启停、连接测试、测试消息和健康状态。
- 增加事件去重、群聊仅响应 @机器人、发送者配对和权限边界；Agent 不可读取或修改明文凭据。
- 不支持个人微信、公众号、企业微信、钉钉或飞书商店应用；首期不处理图片、文件、语音和云文档评论。
- 不改变 `/api/chat` SSE 事件与消息接口，**无 BREAKING**。

## Capabilities

### New Capabilities

- `feishu-channel-runtime`: 飞书 WebSocket 入站、OpenAPI 出站、消息去重、群聊策略、HITL 卡片和运行时生命周期。

### Modified Capabilities

- `agent-delivery`: 将 `feishu` 从配置预留类型升级为可执行 ChannelAdapter，并规定飞书与统一 run、SSOT、delivery result 的关系。
- `agent-hitl`: 增加飞书交互卡片 approve/reject 与文本 clarification 对统一决策模型的映射。

## Impact

- 后端：`noesis_server/domain/chat/delivery/feishu/`、`services/channels/`、`messaging_channel_service.py`、启动编排、通道操作服务与配置。
- 前端：`frontend/src/views/settings/sections/ChannelsSection.vue` 及 settings API 类型。
- API：兼容扩展 `/api/user/channels` 现有请求/响应，不新增匿名入口，不改变 Cookie Session + CSRF。
- 依赖：引入飞书官方 Python SDK；运行环境只需可主动访问飞书公网服务，不要求公网入站地址。
- 数据：飞书应用凭据只从部署环境读取；每用户 `channels.json` 只保存 Open ID、可选 Chat ID 和路由配置，不新增数据库表。
