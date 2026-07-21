## ADDED Requirements

### Requirement: 通道运行时 SHALL 通过 ChannelAdapter 接入 Fan-out

已启用的通讯通道（见设置/配置面）在运行时 SHALL 以 `agent-run-delivery` 所定义的 ChannelAdapter 注册到 ChannelRegistry，入站经 Session/Binding 路由到同一 Agent run 入口，出站订阅 RunEvent 总线。

通道实现 **SHALL NOT** 复制一套独立于消息 SSOT 的 transcript，也 **SHALL NOT** 将浏览器 SSE 连接作为出站前提。

#### Scenario: 配置启用后可解析 adapter

- **WHEN** 用户启用 `type=telegram`（或 `wechat`）通道且运行时已注册对应 adapter 或 stub
- **THEN** ChannelRegistry 按 `channel_type` 解析成功，且入站/出站路径不经过 `LangGraphSseBridge` 字符串 yield

### Requirement: 通道出站投影 SHALL 尊重 adapter capabilities

向 Telegram、微信等平台投递时，系统 SHALL 根据 adapter capabilities 选择流式编辑或仅终态投递，并默认避免将完整工具调用细节镜像到 IM（除非该 adapter 显式启用）。

#### Scenario: 不支持 edit 则终态投递

- **WHEN** 微信（或其它）adapter 声明 `streaming_edit=false` 且 run 完成
- **THEN** 用户在该通道收到的内容 SHALL 基于终态文本投影，而非依赖逐 token SSE 帧转发
