## MODIFIED Requirements

### Requirement: create_noesis_agent 工厂组装

`FaultOperationAgent` SHALL 使用 `create_noesis_agent` 创建运行时 Agent，并满足：

- `tools`：MCP 动态加载的工具列表
- `system_prompt`：故障运维角色提示（中文、Markdown 输出、排查流程与委派规则）
- `checkpointer`：继承自 `BaseAgent` 的会话检查点
- `backend`：`AioSandboxBackend`（见 `agent-sandbox`）经 `create_session_sandbox_backend(user_id, session_id)` 提供；构建前 SHALL 调用 `ensure_workspace_dir`
- `subagents`：至少包含名为 `general-purpose` 的子 Agent，具备与主 Agent 相同的 MCP 工具集与 `build_subagent_default_middleware(backend)` 中间件栈

工厂 SHALL 按 `agent/factory.py` 约定挂载中间件顺序：`FilesystemMiddleware` → `SubAgentMiddleware` → 运行时防护 → `create_agent(model=get_llm(), ...)`。

主 Agent 系统提示 SHALL 规定：复杂、可拆分、可并行的多步排查 MAY 通过 `task` 工具委派 `subagent_type=general-purpose`；简单一两步操作 SHALL NOT 委派。

#### Scenario: 本地工作区隔离

- **WHEN** Agent 通过 `FilesystemMiddleware` 读写排查笔记，且 `run_agent` 传入有效 `session_id` 与 `current_user`
- **THEN** 文件操作 SHALL 限制在当前 session virtual 根，持久化至 `.data/agent_workspace/users/{user_id}/sessions/{session_id}/workspace/`；**SHALL** 使用 **该 session 专属** AIO 容器

#### Scenario: 子 Agent 委派

- **WHEN** 主 Agent 调用 `task` 且 `subagent_type` 为 `general-purpose`
- **THEN** 子 Agent SHALL 在独立上下文中使用相同 MCP 工具完成子任务；SSE 嵌套遵循 `platform-chat`

#### Scenario: 运行时防护生效

- **WHEN** `ModelConfig.tool_call_limit_enabled` 或 `ModelConfig.loop_detection_enabled` 为真
- **THEN** `FaultOperationAgent` SHALL 继承 `create_noesis_agent` 对应中间件

### Requirement: 远程执行与 MCP 安全约束（高层）

故障运维链路 **SHALL** 区分两类执行面：

1. **远程 MCP（SSH）**：MCP 工具 **SHALL** 以只读诊断为设计目标；Agent **SHALL NOT** 假设可执行任意远程写操作。
2. **本地工作区**：**SHALL** 使用 session 级 AIO 沙箱 + virtual `/`；**SHALL NOT** 读取 API 环境或其它 session；**SHALL NOT** 使用 `LocalShellBackend`。

SSH 凭据 **SHALL** 由 MCP 侧配置；MCP URL **SHALL** 可配置；**禁止**在仓库提交生产明文密码。

#### Scenario: Agent 不伪造远程结果

- **WHEN** MCP 工具返回失败或空结果
- **THEN** Agent 回复 SHALL 基于实际工具输出，禁止编造

#### Scenario: 只读诊断边界

- **WHEN** 用户要求变更类操作
- **THEN** Agent SHALL 给诊断与建议，不得规格要求 MCP 支持破坏性写操作

#### Scenario: 本地沙箱不可读 API 环境

- **WHEN** 用户诱导 Agent `execute` 读取 `/app/.env` 或 API 配置
- **THEN** 命令 SHALL 在 **session AIO 容器** 内失败或不可见该路径，**SHALL NOT** 返回 Noesis 业务密钥
