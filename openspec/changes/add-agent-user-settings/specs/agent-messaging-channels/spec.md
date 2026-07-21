## ADDED Requirements

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

### Requirement: Telegram 通道 SHALL 定义入站与出站边界

对 `type=telegram` 且 `enabled=true` 的通道，系统 SHALL 定义：

- **入站**：平台 MAY 将 Telegram 消息路由为该用户的 Agent 请求（默认 `qa_type` 可配置）；实现分期时规格仍要求配置面与数据模型就绪。
- **出站**：定时任务或 Agent 完成结果 MAY 投递到该通道（由任务 `delivery` 或通道路由配置选择）；未配置出站时 **SHALL NOT** 外泄到 Telegram。

入站身份映射 SHALL 绑定到 Noesis `user_id`（例如通过用户在设置中完成的链接/配对流程）；未配对的 Telegram 发送方 **SHALL NOT** 触发任意用户的 Agent。

#### Scenario: 未配对拒绝入站

- **WHEN** 未与任何 Noesis 用户配对的 Telegram 账号向 bot 发消息且入站处理已启用
- **THEN** 系统 SHALL 不执行特权用户的 Agent 任务（可回复引导配对）

#### Scenario: 停用通道停止外发

- **WHEN** 用户将 Telegram 通道 `enabled` 设为 false
- **THEN** 系统 SHALL 停止向该通道投递出站消息

### Requirement: 通道 API SHALL 鉴权且作用域为当前用户

`/api/user/channels`（或等价前缀）SHALL 要求 JWT；列表/变更仅限当前用户。未知 `channel_id` SHALL 返回 404。

#### Scenario: 越权通道

- **WHEN** 用户 A 请求用户 B 的 `channel_id`
- **THEN** SHALL 返回 HTTP 404
