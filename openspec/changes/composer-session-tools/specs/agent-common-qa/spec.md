## MODIFIED Requirements

### Requirement: GeneralQAAgent SHALL 经 create_noesis_agent 装配 LangChain Agent

`GeneralQAAgent`（`backend/agent/common_react_agent.py`）SHALL 继承 `BaseAgent`，在 `run_agent` 内通过 `create_noesis_agent` 创建 Agent 实例；工厂 SHALL 使用 `get_llm()` 作为主模型，并挂载项目统一的运行时防护中间件。

当会话解析出的 `mcp_servers` 非空时，`GeneralQAAgent` SHALL 将对应 MCP 工具并入 `tools` 传给 `create_noesis_agent`。当 `mcp_servers` 为空（含缺省空列表）时，SHALL NOT 挂载 MCP 工具。

COMMON_QA 路径仍 SHALL NOT 默认挂载文件系统 `FilesystemMiddleware`、`SubAgentMiddleware` 或 `task` 委派工具（与是否挂 MCP 无关）。

#### Scenario: 会话勾选 MCP 后 COMMON_QA 可调用工具

- **WHEN** 会话 `extra.mcp_servers` 含有效 server id 且该 server 可连接
- **THEN** `GeneralQAAgent` 的 tools SHALL 包含自该 server 加载的 MCP 工具

#### Scenario: 未勾选 MCP 时行为与历史一致

- **WHEN** 会话无 `mcp_servers` 键或值为空列表
- **THEN** `GeneralQAAgent` SHALL NOT 挂载 MCP 工具
