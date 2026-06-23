## MODIFIED Requirements

### Requirement: create_noesis_agent 工厂组装

`FaultOperationAgent` SHALL 使用 `create_noesis_agent` 创建运行时 Agent，并满足：

- `tools`：MCP 动态加载的工具列表
- `system_prompt`：故障运维角色提示（中文、Markdown 输出、排查流程与委派规则）
- `checkpointer`：继承自 `BaseAgent` 的会话检查点
- `backend`：`LocalShellBackend`，根目录为 `{REPO_ROOT}/.data/users/{user_id}/sessions/{session_id}/workspace/`，`virtual_mode=True`；`user_id` 与 `session_id` 来自 `run_agent` 的 `current_user` 与 `session_id`；构建前 SHALL 调用 `ensure_workspace_dir`（见 `user-data-layout` / `agent-workspace` 规格）
- `subagents`：至少包含名为 `general-purpose` 的子 Agent，具备与主 Agent 相同的 MCP 工具集与 `build_subagent_default_middleware(backend)` 中间件栈

工厂 SHALL 按 `agent/factory.py` 约定挂载中间件顺序：`FilesystemMiddleware` → `SubAgentMiddleware` → 运行时防护（`DanglingToolCallMiddleware`、`ContextEditingMiddleware`、`LoopDetectionMiddleware`、`ToolCallLimitMiddleware` 等，受 `ModelConfig` 开关控制）→ `create_agent(model=get_llm(), ...)`。

主 Agent 系统提示 SHALL 规定：复杂、可拆分、可并行的多步排查 MAY 通过 `task` 工具委派 `subagent_type=general-purpose`；简单一两步操作 SHALL NOT 委派。

#### Scenario: 本地工作区隔离

- **WHEN** Agent 通过 `FilesystemMiddleware` 读写排查笔记或临时脚本，且 `run_agent` 传入有效 `session_id` 与 `current_user`
- **THEN** 文件操作 SHALL 限制在当前会话 `.data/users/{user_id}/sessions/{session_id}/workspace/` 内（`virtual_mode`），不得越界写入宿主机任意路径，**SHALL NOT** 写入遗留全局 `backend/.agent_workspace/fault_ops` 或 `.data/agent_workspace/` 目录

#### Scenario: 子 Agent 委派

- **WHEN** 主 Agent 调用 `task` 且 `subagent_type` 为 `general-purpose`
- **THEN** 子 Agent SHALL 在独立上下文中使用相同 MCP 工具完成子任务，最终仅将汇总结论返回主 Agent；子 Agent 内部 tool/text/reasoning parts 的 SSE 与前端嵌套展示遵循 `platform-chat` 中 `task`/`parentTaskCallId` 要求

#### Scenario: 运行时防护生效

- **WHEN** `ModelConfig.tool_call_limit_enabled` 或 `ModelConfig.loop_detection_enabled` 为真
- **THEN** `FaultOperationAgent` 创建的 Agent SHALL 继承 `create_noesis_agent` 对应中间件，不得绕过工厂自建裸 `create_agent`

### Requirement: 远程执行与 MCP 安全约束（高层）

故障运维链路涉及**两类执行面**，系统 SHALL 在设计与运维上区分约束：

1. **远程 MCP（SSH）**：MCP Server 在隔离环境（如 Docker 容器）内发起 SSH，连接用户指定的目标 `ip`；工具以**只读诊断**为设计目标——`read`/`grep`/`glob` 天然只读，`bash` 仅用于诊断命令（如 `df`、`ps`、`kubectl get` 等），SHALL NOT 用于写文件、修改配置或重启服务。命令风险防控（白名单、黑名单、LLM 二次判断等）由 MCP 侧与 `docs/prd/agent-fault-operation/故障运维设计.md` §4 约定，Agent 侧 SHALL NOT 假设可执行任意 shell。

2. **本地工作区**：`LocalShellBackend` 仅供 Agent 存放**当前会话**排查笔记与中间产物，根目录为 `.data/users/{user_id}/sessions/{session_id}/workspace/`；与远程 MCP 执行面隔离，不得将本地工作区路径作为 MCP `read`/`bash` 的默认目标。

SSH 凭据（`username`/`password`）SHALL 由 MCP 服务配置或调用参数提供，**禁止**在 Noesis 仓库提交的源码或配置中包含生产环境明文密码。MCP 端点 URL SHALL 可配置，默认可指向本地调试地址，生产部署须通过网络策略限制 MCP 服务可达范围。

#### Scenario: Agent 不伪造远程结果

- **WHEN** MCP 工具返回失败或空结果
- **THEN** `FaultOperationAgent` 驱动的模型回复 SHALL 基于实际工具输出组织结论，系统提示 SHALL 禁止编造未执行的命令或其输出
