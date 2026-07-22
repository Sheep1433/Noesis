## ADDED Requirements

### Requirement: SuperAgent 启用 HITL

当 `hitl.enabled=true` 时，`SuperAgent` **SHALL**：

1. 向 `create_noesis_agent` 传入按 `agent-hitl` 规格组装的 `interrupt_on`；
2. 将 `ask_user` 工具并入 `tools` 列表；
3. 为 `task-worker` 子 Agent 传入相同 `interrupt_on`。

当 `hitl.enabled=false` 时，**SHALL NOT** 注册 `ask_user`，**SHALL NOT** 传入 `interrupt_on`。

#### Scenario: 配置开启时 SuperAgent 带 HITL

- **WHEN** `hitl.enabled=true` 且用户发起 `SUPER_AGENT_QA` 流式请求
- **THEN** `SuperAgent` 创建的 Agent **SHALL** 包含 `HumanInTheLoopMiddleware` 与 `ask_user` 工具

#### Scenario: 配置关闭时行为不变

- **WHEN** `hitl.enabled=false`
- **THEN** SuperAgent 工具集与中间件栈 **SHALL** 与变更前一致（无 `ask_user`、无 HITL 中间件）

### Requirement: 交互分流与 HITL 澄清分工

SuperAgent system prompt 的「交互分流」块 **SHALL** 保留：任务入口寒暄、意图不明时 **SHALL** 纯文本回复且 **SHALL NOT** 调用任何工具（含 `ask_user`）。

`ask_user` **SHALL** 仅用于任务已启动、已进入工具循环后的信息补充；prompt **SHALL** 明确二者边界。

#### Scenario: 寒暄不触发 ask_user

- **WHEN** 用户仅发送「你好」
- **THEN** 模型 **SHALL** 文字回复，**SHALL NOT** 调用 `ask_user` 或其它工具

#### Scenario: 任务中途可 ask_user

- **WHEN** 用户已给出具体任务且 Agent 在执行过程中缺少必要参数（如输出格式）
- **THEN** 模型 **MAY** 调用 `ask_user` 并在 HITL 澄清后继续

## MODIFIED Requirements

### Requirement: create_noesis_agent 装配超级智能体能力栈

`SuperAgent` SHALL 通过 `create_noesis_agent` 创建 LangGraph Agent。装配 SHALL 满足：

- `backend`：`create_agent_backend(user_id, session_id)` 提供的 `CompositeBackend`（含 `/research/`、`/memory/`、`/skills/`）；
- `system_prompt`：`build_prompt(PromptProfile.SUPER_AGENT, user_id=...)`；
- `subagents`：至少包含名为 `task-worker` 的同步子 Agent；
- `extra_middleware`（顺序在 `SubAgentMiddleware` 之后、运行时防护之前）：`TodoListMiddleware`、`SkillsMiddleware`、`MemoryMiddleware`（见 `agent-user-memory`）；
- `tools`：`build_web_search_tools()` 返回的 Web 工具列表，且在 `hitl.enabled=true` 时 **SHALL** 追加 `ask_user` 工具（由 `agent-hitl` 模块提供）；
- 当 `hitl.enabled=true` 时 **SHALL** 传入 `interrupt_on`（见 `agent-hitl` 规格）；
- 运行时防护：`build_noesis_runtime_middleware()`（含 `SessionClockMiddleware`、`SummarizationOffloadMiddleware` 等，以 `ModelConfig` 为准）。

`task-worker` 子 Agent **SHALL NOT** 挂载 `MemoryMiddleware`；**SHALL** 继承主 Agent 的 `interrupt_on`（当 HITL 启用时）。

#### Scenario: 主 Agent 具备 task 委派能力

- **WHEN** `SuperAgent` 完成 `create_noesis_agent` 初始化
- **THEN** 主 Agent SHALL 暴露 `task` 工具，且可接受 `subagent_type=task-worker`

#### Scenario: 子 Agent 不加载用户记忆

- **WHEN** 系统构建 `task-worker` 中间件栈
- **THEN** SHALL 包含 `FilesystemMiddleware` 与 `SkillsMiddleware`，**SHALL NOT** 包含 `MemoryMiddleware`

#### Scenario: HITL 启用时子 Agent 继承审批

- **WHEN** `hitl.enabled=true` 且主 Agent 配置了 `interrupt_on`
- **THEN** `task-worker` **SHALL** 使用相同 `interrupt_on` 配置
