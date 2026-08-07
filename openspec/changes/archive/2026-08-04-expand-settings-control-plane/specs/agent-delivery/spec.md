## ADDED Requirements

### Requirement: Delivery SHALL 提供通道运行健康 read model
Delivery SHALL 为设置控制面提供用户作用域的 adapter 状态、最近检查、最近入站/出站结果和脱敏错误摘要。该 read model SHALL 由真实 adapter/runtime 状态派生；通道配置 Service SHALL NOT 写入伪运行状态。

#### Scenario: 获取当前用户通道健康
- **WHEN** 设置服务请求当前用户通道健康摘要
- **THEN** Delivery SHALL 仅返回该用户通道的状态且不包含 token、外部请求 header 或内部堆栈

### Requirement: Delivery SHALL 支持受控测试投递
Delivery SHALL 接受已鉴权设置服务发起的测试投递命令，向指定当前用户通道发送固定测试内容，并返回稳定投递结果。测试投递 SHALL NOT 创建用户聊天消息或触发 Agent run。

#### Scenario: 测试投递成功
- **WHEN** 设置服务对健康且启用的通道发起测试投递
- **THEN** Delivery SHALL 发送固定内容、记录结果并返回关联 id
