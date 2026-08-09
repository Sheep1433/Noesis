## ADDED Requirements

### Requirement: Composer SHALL 不常驻问答类型按钮

聊天 Composer 上方 SHALL NOT 常驻展示 `COMMON_QA`、`SUPER_AGENT_QA` 或 `FAULT_OPERATION_QA` 的切换按钮。附件、模型、知识库、MCP、Skills 与会话文件能力 SHALL 根据当前模式通过紧凑工具菜单提供，并保持原有权限与发送行为。

#### Scenario: 移动端查看空闲 Composer

- **WHEN** 用户在移动端打开没有执行状态的聊天会话
- **THEN** Composer SHALL 只展示输入区域、紧凑工具入口和发送入口
- **AND** Composer 上方 SHALL 不展示问答类型按钮

#### Scenario: 任务模式打开工具菜单

- **WHEN** 用户在“任务”模式打开 Composer 工具菜单
- **THEN** 系统 SHALL 提供该模式支持的附件、模型、知识库、MCP 与 Skills 入口

