## MODIFIED Requirements

### Requirement: CompositeBackend 工作区与 Skills 路由

深度研究文件系统 SHALL 使用 deepagents `CompositeBackend`，满足：

- **默认盘**：`LocalShellBackend(root_dir={REPO_ROOT}/.data/agent_workspace/users/{user_id}/sessions/{session_id}/workspace/, virtual_mode=True)`；`user_id` 来自 `current_user`，`session_id` 来自 `run_agent`；构建前 SHALL `ensure_workspace_dir`；
- **Skills 路由**：`/skills/` → `LocalShellBackend(root_dir=extensions/skills 或 skills_filesystem_root, virtual_mode=True)`，只读语义。

系统 **SHALL NOT** 将遗留 `backend/.agent_workspace` 或 `.data/agent_workspace` 根目录本身（无 `users/.../sessions/...` 段）作为单次运行的可写 backend 根。

#### Scenario: 工作区路径写入

- **WHEN** 主 Agent 或子 Agent 在默认盘创建文件，且 `run_agent` 传入 `session_id=s1` 与有效 `current_user`
- **THEN** 变更 SHALL 落在 `.data/agent_workspace/users/{user_id}/sessions/s1/workspace/` 下，**SHALL NOT** 写入 `extensions/skills` 或遗留 `backend/.agent_workspace`

#### Scenario: 读取 Skills 下 skill 目录

- **WHEN** Agent 访问 `/skills/deep-research-v2/SKILL.md`
- **THEN** SHALL 从 `extensions/skills`（或配置覆盖路径）读取

#### Scenario: 无 backend 时不得挂载子 Agent

- **WHEN** `create_noesis_agent(subagents=..., backend=None)`
- **THEN** 工厂 SHALL 抛出 `ValueError`

### Requirement: 与 skills-filesystem 规格的引用边界

| 维度 | `agent-deep-research` | `skills-filesystem` |
|------|----------------------|---------------------|
| 消费方 | LangGraph Agent / deepagents | Skills 管理页 |
| 访问面 | `FilesystemMiddleware` + `/skills/` | `GET/POST /api/skills/fs/*` |
| 写入范围 | 当前会话 `.data/agent_workspace/users/.../workspace/` | ZIP → 技能根目录 |
| 认证 | 聊天 JWT 链路内进程访问 | 接口 JWT |

#### Scenario: 运维上传新 skill 后 Agent 可见

- **WHEN** 运维经 skills-filesystem API 写入 `extensions/skills`
- **THEN** 后续 `DEEP_RESEARCH_QA` 会话 SHALL 能在 `/skills/` 发现该 skill

#### Scenario: Agent 规格不定义 HTTP 树接口

- **WHEN** 审查者仅阅读本规格
- **THEN** 不得要求实现 `GET /api/skills/fs/tree`；该行为引用 `skills-filesystem`
