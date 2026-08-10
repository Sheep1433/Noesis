## ADDED Requirements

### Requirement: 飞书运行时 SHALL 使用企业自建应用长连接接收入站事件
系统 SHALL 在 `messaging.feishu_runtime_enabled=true` 且部署级飞书应用凭据完整时启动一个官方 SDK WebSocket 客户端；同一进程内所有已启用飞书用户绑定 SHALL 共享该应用连接。关闭开关后 SHALL 停止处理入站事件，且 SHALL NOT 要求公网入站 URL。

#### Scenario: 开关关闭不建立连接
- **WHEN** `messaging.feishu_runtime_enabled=false`
- **THEN** 系统 SHALL NOT 启动飞书 WebSocket 客户端

#### Scenario: 多个用户共享应用连接
- **WHEN** 两个 Noesis 用户分别绑定不同的飞书 Open ID
- **THEN** 系统 SHALL 通过同一个飞书应用连接接收两人的消息
- **AND** SHALL 按 Open ID 将消息路由到各自的 Noesis 用户、session 与 Agent 配置

### Requirement: 飞书事件 SHALL 快速确认并异步执行
消息与卡片事件 handler SHALL 在平台确认时限内完成校验和入队，SHALL NOT 在 handler 内等待 LLM、工具或完整 Agent run；异步任务失败 SHALL 记录到通道健康状态。

#### Scenario: Agent 执行耗时超过确认窗口
- **WHEN** 一个合法消息触发耗时 Agent run
- **THEN** 飞书事件 handler SHALL 先成功返回
- **AND** Agent SHALL 在后台继续执行并投递结果

### Requirement: 飞书文本入站 SHALL 执行配对、群聊与幂等策略
系统 SHALL 以发送者 `open_id` 进行用户配对；单聊文本可直接触发，群聊文本只有明确 @机器人时才可触发并 SHALL 去除机器人 mention。系统 SHALL 以 `event_id` 或 `message_id` 去重，未配对、重复或不受支持的消息 SHALL NOT 触发 Agent。

#### Scenario: 已配对单聊触发 Agent
- **WHEN** 已配对 open_id 向机器人发送文本
- **THEN** 系统 SHALL 将文本写入绑定 session 的消息 SSOT 并启动 headless run

#### Scenario: 群聊没有提及机器人
- **WHEN** 群成员发送未 @机器人的普通文本
- **THEN** 系统 SHALL 忽略该事件且 SHALL NOT 写入用户消息

#### Scenario: 飞书重推同一事件
- **WHEN** 系统再次收到相同 event_id 或 message_id
- **THEN** 系统 SHALL NOT 重复写入消息或启动第二次 run

### Requirement: 飞书出站 SHALL 支持节流更新与终态回落
系统 SHALL 将 Agent 可见文本投影到原会话，流式更新 SHALL 节流；工具内容默认只显示名称与短状态。卡片更新失败或内容超过平台限制时 SHALL 回落为分段终态文本，完整工具 output 与敏感数据 SHALL NOT 发往飞书。

#### Scenario: 无浏览器连接仍收到回复
- **WHEN** 飞书消息触发 run 且没有浏览器 SSE subscriber
- **THEN** 用户 SHALL 在飞书收到 Agent 终态回复

#### Scenario: 飞书投递失败
- **WHEN** Agent run 已完成但飞书 API 返回错误
- **THEN** run SHALL 保持原 completed 状态
- **AND** 通道 SHALL 独立记录投递失败与可定位信息

### Requirement: 飞书运行时 SHALL 提供健康与连接操作
通道操作服务 SHALL 使用部署级应用凭据校验共享应用、向用户配置的目标发送测试消息，并记录连接状态、最近入站、最近出站和脱敏错误；用户通道 API SHALL NOT 接收或返回 App ID、App Secret 或 access token。

#### Scenario: 测试合法飞书配置
- **WHEN** 已认证用户对自己的飞书通道执行连接测试
- **THEN** 系统 SHALL 使用运行时凭据校验应用并返回脱敏健康摘要
