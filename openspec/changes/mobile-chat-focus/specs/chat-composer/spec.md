## ADDED Requirements

### Requirement: Composer SHALL 不常驻问答类型按钮

聊天 Composer 上方 SHALL NOT 常驻展示 `COMMON_QA`、`SUPER_AGENT_QA` 或 `FAULT_OPERATION_QA` 的切换按钮。模型 SHALL 作为高频设置常驻在 Composer `+` 号旁边；附件、知识库、MCP、Skills 与会话文件能力 SHALL 根据当前模式通过紧凑工具菜单提供，并保持原有权限与发送行为。

#### Scenario: 移动端查看空闲 Composer

- **WHEN** 用户在移动端打开没有执行状态的聊天会话
- **THEN** Composer SHALL 展示输入区域、紧凑工具入口、模型切换入口和发送入口
- **AND** Composer 上方 SHALL 不展示问答类型按钮

#### Scenario: 任务模式打开工具菜单

- **WHEN** 用户在“任务”模式打开 Composer 工具菜单
- **THEN** 系统 SHALL 提供该模式支持的附件、知识库、MCP 与 Skills 入口
- **AND** 模型切换 SHALL 位于工具菜单外的 `+` 号旁边

### Requirement: Composer 占位文案 SHALL 使用产品语言

Composer 占位文案 SHALL 简洁说明 `/` 与 `@` 的可用能力，SHALL NOT 使用“行首”“空格后”等解析规则描述，也 SHALL NOT 暴露 `subagent` 等内部术语。

#### Scenario: 任务模式查看输入框

- **WHEN** 用户打开任务模式 Composer
- **THEN** 占位文案 SHALL 使用“Skill”“文件”“协作助手”等用户可理解的名称说明快捷能力
