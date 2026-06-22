## MODIFIED Requirements

### Requirement: create_noesis_agent 工厂组装

`FaultOperationAgent` SHALL 使用 `create_noesis_agent`，且：

- `backend`：`AioSandboxBackend` 经 `create_user_sandbox_backend(user_id, session_id)`；**用户级** AIO 容器；构建前 `ensure_workspace_dir`
- 其余：`tools`（MCP）、`system_prompt`、`checkpointer`、`subagents`（general-purpose）同现规格

#### Scenario: 本地工作区隔离

- **WHEN** Agent 读写排查笔记，且 `session_id` 与 `current_user` 有效
- **THEN** filesystem 默认操作 SHALL 落在当前 session workspace；**MAY** 与同用户其它 session **共用** 一个 AIO 容器

#### Scenario: 子 Agent 委派

- **WHEN** 主 Agent 调用 `task` 且 `subagent_type` 为 `general-purpose`
- **THEN** 子 Agent SHALL 在独立上下文中使用相同 MCP 工具；SSE 嵌套遵循 `platform-chat`

#### Scenario: 运行时防护生效

- **WHEN** `ModelConfig.tool_call_limit_enabled` 或 `ModelConfig.loop_detection_enabled` 为真
- **THEN** `FaultOperationAgent` SHALL 继承 `create_noesis_agent` 对应中间件

### Requirement: 远程执行与 MCP 安全约束（高层）

1. **远程 MCP（SSH）**：只读诊断；**SHALL NOT** 假设任意远程写。
2. **本地工作区**：**用户级** AIO 沙箱 + session virtual `/`；**SHALL NOT** 使用 `LocalShellBackend`；**SHALL NOT** 读取 API 环境或其它 **用户** workspace。

#### Scenario: Agent 不伪造远程结果

- **WHEN** MCP 工具返回失败或空结果
- **THEN** Agent 回复 SHALL 基于实际工具输出

#### Scenario: 只读诊断边界

- **WHEN** 用户要求变更类操作
- **THEN** Agent SHALL 给诊断与建议

#### Scenario: 本地沙箱不可读 API 环境

- **WHEN** 用户诱导 Agent `execute` 读取 `/app/.env`
- **THEN** **SHALL NOT** 返回 Noesis 业务密钥

#### Scenario: 同用户跨 session 排查笔记（未来）

- **WHEN** Agent 经 `execute` 读取同用户其它 session 的 workspace 文件
- **THEN** **MAY** 成功（同一 AIO 容器 mount）
