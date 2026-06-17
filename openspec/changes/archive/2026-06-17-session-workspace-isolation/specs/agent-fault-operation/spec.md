## MODIFIED Requirements

### Requirement: create_noesis_agent 工厂组装

`FaultOperationAgent` SHALL 使用 `create_noesis_agent`，并满足：

- `backend`：`LocalShellBackend`，根为 `{REPO_ROOT}/.data/agent_workspace/users/{user_id}/sessions/{session_id}/workspace/`，`virtual_mode=True`；构建前 `ensure_workspace_dir`
- 其余装配（`tools`、MCP、`subagents`、中间件栈）不变。

**SHALL NOT** 使用遗留 `backend/.agent_workspace/fault_ops` 或任何全局共享可写根。

#### Scenario: 本地工作区隔离

- **WHEN** Agent 读写排查笔记，且 `run_agent` 有有效 `session_id` 与 `current_user`
- **THEN** 操作 SHALL 限制在 `.data/agent_workspace/users/{user_id}/sessions/{session_id}/workspace/` 内

#### Scenario: 子 Agent 委派

- **WHEN** 主 Agent `task` 委派 `general-purpose`
- **THEN** 子 Agent 使用相同 MCP 工具；SSE 嵌套遵循 `platform-chat`

#### Scenario: 运行时防护生效

- **WHEN** `ModelConfig` 启用 tool call limit 或 loop detection
- **THEN** SHALL 经 `create_noesis_agent` 挂载，不得绕过工厂

### Requirement: 远程执行与 MCP 安全约束（高层）

1. **远程 MCP（SSH）**：只读诊断语义不变。
2. **本地工作区**：根为 `.data/agent_workspace/users/{user_id}/sessions/{session_id}/workspace/`，与 MCP 远程执行面隔离。

#### Scenario: Agent 不伪造远程结果

- **WHEN** MCP 工具失败或空结果
- **THEN** 回复 SHALL 基于实际输出，禁止编造

#### Scenario: 只读诊断边界

- **WHEN** 用户要求变更类操作（删日志、重启服务）
- **THEN** Agent SHALL 给诊断与建议，不得规格要求 MCP 支持破坏性写操作
