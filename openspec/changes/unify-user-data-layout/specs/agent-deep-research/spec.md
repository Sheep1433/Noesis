## MODIFIED Requirements

### Requirement: CompositeBackend 工作区与 Skills 路由

深度研究文件系统 SHALL 使用 deepagents `CompositeBackend`，满足：

- **默认盘**：`LocalShellBackend(root_dir={REPO_ROOT}/.data/users/{user_id}/sessions/{session_id}/workspace/, virtual_mode=True)`，其中 `user_id` 来自 `current_user`，`session_id` 来自 `run_agent` 参数；目录 SHALL 在构建 backend 前通过 `ensure_workspace_dir` 创建；
- **平台 Skills 路由**：路径前缀 `/skills/` 映射至 `LocalShellBackend(root_dir=extensions/skills（或 skills_filesystem_root 配置）, virtual_mode=True)`；
- **用户 Skills 路由**：路径前缀 `/user-skills/` 映射至 `LocalShellBackend(root_dir=.data/users/{user_id}/skills/, virtual_mode=True)`；
- Agent 侧对 `/skills/` 与 `/user-skills/` 的访问语义为 **只读技能包挂载**。

`SkillsMiddleware` **SHALL** 使用 `sources=["/skills/", "/user-skills/"]`。

系统 **SHALL NOT** 将遗留全局 `backend/.agent_workspace` 或 `.data/agent_workspace` 作为可写 backend 根。

#### Scenario: 工作区路径写入

- **WHEN** 主 Agent 或子 Agent 通过文件系统工具在默认路径创建或修改文件，且 `run_agent` 传入 `session_id=s1` 与有效 `current_user`
- **THEN** 变更 SHALL 落在 `.data/users/{user_id}/sessions/s1/workspace/` 虚拟根下，**SHALL NOT** 直接写入 `extensions/skills` 或 `.data/users/{uid}/skills/`

#### Scenario: 读取平台 Skills

- **WHEN** Agent 访问 `/skills/deep-research-v2/SKILL.md`
- **THEN** 系统 SHALL 从 `extensions/skills` 对应路径读取

#### Scenario: 读取用户 Skills

- **WHEN** Agent 访问 `/user-skills/my-skill/SKILL.md`
- **THEN** 系统 SHALL 从 `.data/users/{user_id}/skills/my-skill/SKILL.md` 读取

#### Scenario: 无 backend 时不得挂载子 Agent

- **WHEN** `run_agent` 缺少 `session_id` 或 `current_user` 导致无法构建 workspace backend
- **THEN** 系统 **SHALL NOT** 以全局可写目录代替；**SHALL** 按现有规格中止或降级（无文件系统工具）

### Requirement: create_noesis_agent 装配深度研究能力栈

`DeepResearchAgent` SHALL 通过 `create_noesis_agent` 创建 LangGraph Agent，且 **SHALL NOT** 在本场景单独调用裸 `create_agent` 绕过统一中间件顺序。装配 SHALL 满足：

- `backend`：由 `_build_research_backend()` 提供的 `CompositeBackend`；
- `subagents`：至少包含名为 `research-worker` 的同步子 Agent 规格；
- `extra_middleware`：主 Agent 挂载 `SkillsMiddleware(backend=backend, sources=["/skills/", "/user-skills/"])`；
- `tools`：空列表（文件系统工具由 `FilesystemMiddleware` 注入，**SHALL NOT** 重复注册）；
- `checkpointer`：继承 `BaseAgent` 的 `InMemorySaver`；
- 运行时防护栈：经 `build_noesis_runtime_middleware()` 挂载（含可选 `SummarizationOffloadMiddleware`、`ContextEditingMiddleware`、`LoopDetectionMiddleware`、`ToolCallLimitMiddleware` 等，以 `ModelConfig` 为准）。

中间件顺序 SHALL 与 `agent/factory.py` 文档一致：`FilesystemMiddleware` → `SubAgentMiddleware` → `extra_middleware`（Skills）→ 运行时防护 → `ToolCallLimitMiddleware`（尾栈）。

#### Scenario: 主 Agent 具备 task 委派能力

- **WHEN** `DeepResearchAgent` 完成 `create_noesis_agent` 初始化
- **THEN** 主 Agent SHALL 暴露 `task` 工具（由 `SubAgentMiddleware` 提供），且可接受 `subagent_type=research-worker`

#### Scenario: 子 Agent 默认中间件

- **WHEN** 系统构建 `research-worker` 子 Agent 中间件栈
- **THEN** SHALL 包含 `FilesystemMiddleware(backend=backend)` 与 `build_subagent_default_middleware` 中的运行时防护，并追加 `SkillsMiddleware(sources=["/skills/", "/user-skills/"])`
