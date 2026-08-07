# channel-operations Specification

## Purpose
TBD - created by archiving change expand-settings-control-plane. Update Purpose after archive.
## Requirements
### Requirement: 通道设置 SHALL 展示权威健康与最近活动
系统 SHALL 为当前用户每条通道展示配置启用状态、adapter 运行状态、最近检查时间、最近入站/出站结果和脱敏错误摘要。健康数据 SHALL 来源于 Delivery/adapter read model，设置层 SHALL NOT 维护第二套运行状态。

#### Scenario: Adapter 离线
- **WHEN** 已启用通道的 adapter 当前不可用
- **THEN** 设置页 SHALL 显示 degraded/unavailable 状态、检查时间和可行动提示

### Requirement: 用户 SHALL 测试通道连接与投递
用户 SHALL 能执行不产生聊天消息的连接测试，以及发送固定产品内容的测试投递。测试 SHALL 受当前用户通道作用域、速率限制和超时约束，并记录脱敏结果与审计。

#### Scenario: 测试 Telegram 投递
- **WHEN** 已配对用户对启用的 Telegram 通道发送测试消息
- **THEN** adapter SHALL 向绑定目标发送固定测试内容并在设置页返回投递结果

### Requirement: 通道默认路由 SHALL 受用户与能力边界约束
通道设置 SHALL 支持默认 `qa_type`、会话策略和投递偏好；配置 SHALL 仅能引用当前用户可用对象。禁用通道 SHALL NOT 接收入站 Agent run，也 SHALL NOT 产生出站投递。

#### Scenario: 禁用通道
- **WHEN** 用户关闭通道启用状态
- **THEN** 后续入站 SHALL NOT 触发 Agent 且后续出站 SHALL NOT 投递到该通道

