## ADDED Requirements

### Requirement: SuperAgent SHALL 提供用户记忆检索工具

SuperAgent SHALL 获得 `search_memory` 工具，支持查询词、可选日期范围、分类和 top_k。工具 SHALL 在运行时绑定当前 user_id，模型不得指定或覆盖用户标识，返回内容 SHALL 为精简 L2 条目而非整篇文件。

#### Scenario: Agent 跨会话检索
- **WHEN** 用户问题需要回忆其他会话的信息且 Agent 调用 search_memory
- **THEN** 工具 SHALL 只返回当前用户匹配的记忆摘要、日期、分数和来源标识

### Requirement: SuperAgent SHALL 提供记忆来源读取工具

SuperAgent SHALL 获得 `get_memory_source` 工具，并在数据库层校验 session 和 message 均属于当前用户。工具 SHALL 限制相邻消息数量并排除 reasoning 与工具原始输出。

#### Scenario: Agent 追溯来源
- **WHEN** Agent 使用搜索结果的 session_id/message_id 请求来源
- **THEN** 工具 SHALL 返回有限的可见文本上下文

#### Scenario: 越权来源请求
- **WHEN** 请求的来源不属于当前用户
- **THEN** 工具 SHALL 返回不存在或无权限且不得泄露来源内容
