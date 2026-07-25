# agent-messaging-channels Specification

## Purpose
TBD - created by archiving change unify-run-delivery. Update Purpose after archive.
## Requirements
### Requirement: 通道运行时归属本 Delivery；配置面归属 settings

通道 **配置、密钥、配对持久化与设置 UI** SHALL 由 `add-agent-user-settings`（`agent-messaging-channels` 配置面）提供。本 delta **仅**覆盖运行时：ChannelAdapter 注册、入站路由、出站投影、与 RunEvent Fan-out 的衔接。

本能力 **SHALL NOT** 再定义一套用户可写的通道 CRUD / token 存储；**SHALL** 读取 settings 已持久化的配置与绑定。

#### Scenario: 运行时消费已保存配置

- **WHEN** 用户已在设置中启用某 `channel_type` 且 pairing 有效
- **THEN** ChannelRegistry / Binding 解析 SHALL 能基于该持久化配置启动入站或出站，而无需在 Delivery 内重复保存 token

### Requirement: 通道运行时 SHALL 通过 ChannelAdapter 接入 Fan-out

已启用的通讯通道在运行时 SHALL 以 `agent-run-delivery` 所定义的 ChannelAdapter 注册到 ChannelRegistry，入站经 Session/Binding 路由到同一 Agent run 入口，出站订阅 RunEvent 总线。

通道实现 **SHALL NOT** 复制一套独立于消息 SSOT 的 transcript，也 **SHALL NOT** 将浏览器 SSE 连接作为出站前提。

#### Scenario: 配置启用后可解析 adapter

- **WHEN** 用户启用 `type=telegram`（或 `wechat`）通道且运行时已注册对应 adapter 或 stub
- **THEN** ChannelRegistry 按 `channel_type` 解析成功，且入站/出站路径不经过 `LangGraphSseBridge` 字符串 yield

### Requirement: 通道出站投影 SHALL 尊重 adapter capabilities

向 Telegram、微信等平台投递时，系统 SHALL 根据 adapter capabilities 选择流式编辑或仅终态投递，并默认避免将完整工具调用细节镜像到 IM（除非该 adapter 显式启用）。

#### Scenario: 不支持 edit 则终态投递

- **WHEN** 微信（或其它）adapter 声明 `streaming_edit=false` 且 run 完成
- **THEN** 用户在该通道收到的内容 SHALL 基于终态文本投影，而非依赖逐 token SSE 帧转发

### Requirement: 系统 SHALL 提供可扩展的通讯通道配置模型

系统 SHALL 为每个用户持久化零个或多个通讯通道配置，每条至少包括：`channel_id`、`type`、`enabled`、`display_name`、通道特定连接参数。首期 `type` SHALL 包含 `telegram`；模型 SHALL 允许后续增加其它 `type`（如飞书）而不改变设置壳导航结构。

通道密钥与 token **SHALL** 仅经由已认证用户的设置 UI 或 `/api/user/channels`（或等价）API 写入；**SHALL NOT** 存入 `USER.md` / `AGENTS.md` / 日记文件；Agent 工具 **SHALL NOT** 读取明文 token 或修改通道密钥字段。

#### Scenario: 保存 Telegram 通道

- **WHEN** 用户提交 `type=telegram` 与合法 bot token 的创建/更新请求
- **THEN** 系统 SHALL 持久化通道且 `enabled` 可按请求设置；后续 GET **SHALL** 对 token 脱敏（例如仅后缀）

#### Scenario: Agent 不可改通道密钥

- **WHEN** Agent 尝试通过文件工具或其它工具写入通道配置或 token
- **THEN** 系统 SHALL 拒绝或使该路径不可达，通道配置保持不变

### Requirement: 设置页 channels section SHALL 管理通道

设置壳 `channels` section SHALL 列出用户通道（类型、显示名、启用状态、连接健康摘要若可得），并支持添加/编辑/启停/删除 Telegram 通道。

若入站/出站运行时尚未实现，UI SHALL 仍允许保存配置，并明示「消息收发将在通道启用后生效」或等价状态，**SHALL NOT** 因 runtime stub 而隐藏整个 `channels` section。

#### Scenario: 列出通道

- **WHEN** 用户打开 `channels` 且已配置一条 Telegram 通道
- **THEN** 列表 SHALL 显示该通道且不展示完整明文 token

### Requirement: Telegram 通道配置 SHALL 声明产品边界（运行时另见 Delivery）

对 `type=telegram` 的通道配置，系统 SHALL 能持久化：`enabled`、连接参数、可选默认 `qa_type`、以及与 Noesis `user_id` 的配对/绑定标识（存储格式由实现选定）。

下列**运行时**行为（入站路由、出站 Fan-out、Adapter 收发）**SHALL** 由 `unify-run-delivery` / ChannelAdapter 实现；本能力 **SHALL** 仅保证配置与配对数据可被运行时读取，**SHALL NOT** 在本 change 内要求落地 webhook/long-poll 或消息投递管线。

产品边界（供运行时遵守）：

- 未配对的 Telegram 发送方 **SHALL NOT** 触发任意用户的 Agent；
- 通道 `enabled=false` 时 **SHALL NOT** 向外投递；
- 未配置出站目标时 **SHALL NOT** 将 Agent/cron 结果外泄到 Telegram。

#### Scenario: 保存启用中的 Telegram 配置

- **WHEN** 用户保存 `type=telegram` 且 `enabled=true` 的通道配置（含脱敏后的连接信息）
- **THEN** 系统 SHALL 持久化该配置，供后续 ChannelAdapter 读取

#### Scenario: 停用配置可供运行时读取

- **WHEN** 用户将 Telegram 通道 `enabled` 设为 false 并保存
- **THEN** 持久化状态 SHALL 为 disabled；运行时（Delivery）据此 **SHALL** 停止外发（本 change 不实现外发本身）

### Requirement: 通道 API SHALL 鉴权且作用域为当前用户

`/api/user/channels`（或等价前缀）SHALL 要求有效的 Cookie Session（与 `user-auth` 一致）；非安全方法 SHALL 校验 CSRF。列表/变更仅限当前用户。未知 `channel_id` SHALL 返回 404。系统 **SHALL NOT** 以 Authorization Bearer JWT 作为本 API 的身份凭据。

#### Scenario: 越权通道

- **WHEN** 用户 A 请求用户 B 的 `channel_id`
- **THEN** SHALL 返回 HTTP 404

### Requirement: telegram ChannelAdapter SHALL 可真收发（非永久 stub）

当 Telegram 运行时启用时，`channel_type=telegram` 的注册实现 SHALL 支持 normalize 入站 Update 与出站 `sendMessage` 投影；**SHALL NOT** 仅停留在无副作用的 stub（测试可用 stub 替换）。

#### Scenario: Registry 解析到可运行 adapter

- **WHEN** 运行时已启动且存在启用中的 telegram 通道
- **THEN** ChannelRegistry 对 `telegram` 的解析结果 SHALL 能执行入站规范化与出站发送接口
