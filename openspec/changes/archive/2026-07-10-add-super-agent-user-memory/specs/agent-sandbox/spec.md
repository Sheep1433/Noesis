## ADDED Requirements

### Requirement: 用户记忆文件位于 AIO mount 根

AIO 容器 rw mount `{NOESIS_HOST_DATA_DIR}/users/{user_id}/` → `/workspace` **SHALL** 使下列宿主机文件在容器内可直接访问：

- `/workspace/AGENTS.md`
- `/workspace/USER.md`

上述文件 **SHALL** 与 `users/{uid}/sessions/{sid}/workspace/` 平级，**SHALL NOT** 仅存在于某一 session workspace 子目录内。

#### Scenario: 容器内读取 AGENTS.md

- **WHEN** 同用户 AIO 容器运行中，Agent backend 解析 `/memory/AGENTS.md`
- **THEN** 读写 SHALL 映射到容器 `/workspace/AGENTS.md` 与宿主机 `users/{uid}/AGENTS.md`

## MODIFIED Requirements

### Requirement: Agent virtual 根 SHALL 对应当前 session workspace

`create_agent_backend(user_id, session_id)` SHALL 使 deepagents virtual **`/research/`**（或等效 session 工作区路由）映射到容器内 **`/workspace/sessions/{session_id}/workspace`**；用户记忆 virtual **`/memory/`** **SHALL** 映射到 **`/workspace/`** 根下的 `AGENTS.md` 与 `USER.md`，**SHALL NOT** 映射到 session workspace 子目录。

Agent **SHALL** 继续使用 `/research/...`、`/memory/...`、`/skills/...` 虚拟路径，**SHALL NOT** 在 Prompt 中嵌入 `sessions/{session_id}/` 物理路径。

#### Scenario: 工作区写入

- **WHEN** session `s1` 写入 `/research/plan.md`
- **THEN** 文件 SHALL 落在 `.data/users/{uid}/sessions/s1/workspace/research/plan.md`（或等价映射路径）

#### Scenario: 记忆与工作区路径分离

- **WHEN** session `s1` 写入 `/memory/AGENTS.md` 与 `/research/plan.md`
- **THEN** 前者 SHALL 落在 `users/{uid}/AGENTS.md`，后者 SHALL 落在 `sessions/s1/workspace/` 下，**SHALL NOT** 为同一目录
