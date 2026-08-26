## ADDED Requirements

### Requirement: ToolPart SHALL 持久化 Run evidence 所需的内部 provenance

每个 in-scope 工具调用的后端持久化 ToolPart SHALL 保存稳定 `provider_key`、可选 `provider_version`、结构化 lifecycle state、execution outcome、tool call id、parent/step 关联和受控的 evidence classification，供终态 Run capture、scope 计算、来源追溯和记忆安全门控使用。内置工具 SHALL 使用稳定内置标识；MCP 工具 SHALL 使用不会因展示名称变化而改变的服务标识；无法确定时 SHALL 写入明确 unknown 值而非按当前配置猜测历史来源。

这些字段是后端内部证据元数据，SHALL NOT 出现在用户可见 SSE、聊天历史 API、前端 tool card、用户错误文案或模型可控制的参数中。任何向客户端序列化 ToolPart 的路径 SHALL 显式剥离内部 provenance、provider 地址和服务端路径。记忆 capture SHALL 使用所有成功/失败/拒绝/超时 ToolPart，不得以 outcome 是否失败决定 Run 是否 eligible。

#### Scenario: 成功工具保留来源和 outcome
- **WHEN** `search_knowledge_base` 成功并持久化 ToolPart
- **THEN** 后端 ToolPart SHALL 包含稳定内部 provider、success outcome 和 tool call id
- **AND** 终态 Run capture SHALL 能把它作为 workflow/experience evidence

#### Scenario: 失败工具保留结构化 outcome
- **WHEN** 工具执行失败、超时或被拒绝
- **THEN** 后端 ToolPart SHALL 保留调用层 state 与执行层 outcome
- **AND** memory capture SHALL NOT 依赖用户可见错误短句重新推断失败类型

#### Scenario: MCP 工具记录稳定来源
- **WHEN** 来自某 MCP server 的工具完成
- **THEN** 持久化 ToolPart SHALL 包含该 server 的稳定 provider key 和可得版本
- **AND** SHALL NOT 依赖模型输出或事后扫描当前配置推断历史来源

#### Scenario: provider 无法确定
- **WHEN** 工具运行时无法取得稳定 provider identity
- **THEN** 持久化元数据 SHALL 使用明确 unknown 值
- **AND** SHALL NOT 将其它同名工具的 provider 误绑定到本次调用

#### Scenario: 用户可见协议不泄露内部 provenance
- **WHEN** 客户端订阅工具 SSE、刷新历史消息或展开工具卡片
- **THEN** 响应 SHALL NOT 暴露 provider key、provider version、内部 server 名称、网络位置、evidence classification 或服务端路径
