## MODIFIED Requirements

### Requirement: FaultOperationAgent SHALL 按会话 mcp_servers 加载 MCP

`FaultOperationAgent` SHALL 使用 `qa_service` 解析后的 MCP 工具列表（来自会话 `extra.mcp_servers`，缺省回退平台 profile `fault_operation`），注入主 Agent 与 `general-purpose` 子 Agent。SHALL NOT 在忽略会话勾选的情况下始终硬绑单一 profile（回退仅适用于键缺失）。

#### Scenario: 会话显式选择用户 MCP

- **WHEN** 故障运维会话 `extra.mcp_servers` 为用户自定义 server id 列表
- **THEN** Agent SHALL 仅加载这些 server 的工具
