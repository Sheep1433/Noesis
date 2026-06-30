## ADDED Requirements

### Requirement: Purpose

本能力规定 Noesis **用户级 Agent 记忆**的磁盘布局、Agent 虚拟路径、`MemoryMiddleware` 装配与写入边界。记忆文件跨会话持久，与 session workspace 任务产物分离；平台开发者文档（仓库根 `AGENTS.md`）**SHALL NOT** 作为记忆源注入。

### Requirement: 用户记忆磁盘路径

对每个合法 `user_id`，系统 SHALL 使用下列路径（相对于 `.data/users/{user_id}/`）：

| 文件 | 用途 | 删除会话时 |
|------|------|------------|
| `AGENTS.md` | 用户惯例、偏好、Agent 学习到的稳定事实 | **保留** |
| `USER.md` | 用户画像（姓名、部门、角色等） | **保留** |

`delete_session_data` **SHALL NOT** 删除上述文件。

路径模块 **SHALL** 提供：

- `get_user_agents_md_path(user_id)` → `.../users/{uid}/AGENTS.md`
- `get_user_profile_md_path(user_id)` → `.../users/{uid}/USER.md`
- `ensure_user_memory_files(user_id)`：创建用户根目录；若 `AGENTS.md` 不存在则写入 seed 模板；若 `USER.md` 不存在则写入 seed 模板（可为最小占位）

#### Scenario: 解析用户 AGENTS.md 路径

- **WHEN** 调用 `get_user_agents_md_path("42")`
- **THEN** 返回路径 SHALL 等于 `{REPO_ROOT}/.data/users/42/AGENTS.md`

#### Scenario: 删会话保留记忆文件

- **WHEN** 对 session `s1` 调用 `delete_session_data(uid, s1)` 且用户根下存在 `AGENTS.md`
- **THEN** `AGENTS.md` SHALL 仍然存在且内容不变

### Requirement: Agent 虚拟路径 /memory/

`CompositeBackend` SHALL 为当前 `user_id` 暴露路由 `/memory/`，至少包含：

- `/memory/AGENTS.md` → 宿主机 `users/{uid}/AGENTS.md`（可写）
- `/memory/USER.md` → 宿主机 `users/{uid}/USER.md`（**Agent 侧只读**）

Agent **SHALL NOT** 通过 `/research/` 路径读写用户记忆文件。

#### Scenario: 主 Agent 读取用户记忆

- **WHEN** Agent `read_file("/memory/AGENTS.md")`
- **THEN** SHALL 返回该用户 `AGENTS.md` 正文

#### Scenario: Agent 拒绝写入 USER.md

- **WHEN** Agent `write` 或 `edit` `/memory/USER.md`
- **THEN** backend SHALL 返回权限错误，**SHALL NOT** 修改磁盘文件

#### Scenario: Agent 可写 AGENTS.md

- **WHEN** Agent `edit_file` 修改 `/memory/AGENTS.md` 且内容合法
- **THEN** 变更 SHALL 持久化到 `users/{uid}/AGENTS.md`

### Requirement: MemoryMiddleware 装配

`SuperAgent` 主 Agent SHALL 挂载 deepagents `MemoryMiddleware`，配置 SHALL 满足：

- `sources` 顺序为 `["/memory/USER.md", "/memory/AGENTS.md"]`（缺失文件跳过）；
- `system_prompt` 使用 Noesis 中文模板 `NOESIS_MEMORY_SYSTEM_PROMPT`，且 **SHALL** 包含 `{agent_memory}` 占位符；
- 注入内容 **SHALL** 包装为 `<agent_memory>` 块并追加至 system message（由 middleware 完成）。

`NOESIS_MEMORY_SYSTEM_PROMPT` **SHALL** 规定：

- `USER.md` 为只读参考，Agent **SHALL NOT** 通过工具修改；
- `AGENTS.md` 可在用户明确要求记住或发现稳定偏好时通过 `edit_file` 更新；
- **SHALL NOT** 写入 API Key、密码、token；
- **SHALL NOT** 写入一次性任务结果、易过期 artifact（PR 号、commit SHA 等）；
- 记忆与用户当前消息冲突时以用户消息为准。

#### Scenario: 首次会话注入 seed 记忆

- **WHEN** 用户首次运行 `SuperAgent` 且 `AGENTS.md` 仅为 seed 模板
- **THEN** system message SHALL 包含 `<agent_memory>` 块，且不为空提示符（至少含 seed 内容或显式「暂无」）

#### Scenario: 子 Agent 不注入记忆

- **WHEN** `task-worker` 发起 model 调用
- **THEN** system message **SHALL NOT** 包含 `MemoryMiddleware` 注入的 `<agent_memory>` 块

### Requirement: 记忆与会话内同步

系统 **SHALL** 在 Agent 成功写入 `/memory/AGENTS.md` 后更新当前 run 的 `memory_contents` state（或等效机制），使同会话后续 model 调用可见最新内容。

上下文压缩或显式 prompt 重建边界 **SHALL** 从磁盘重新加载 `/memory/*.md`。

#### Scenario: 同会话编辑后可见

- **WHEN** 主 Agent 在同一 session 内 `edit_file` 更新 `/memory/AGENTS.md` 并立即再次调用模型
- **THEN** 后续 model 请求的 system message SHALL 反映更新后正文

### Requirement: 平台 AGENTS.md 不得注入

系统 **SHALL NOT** 将仓库根目录 `AGENTS.md`、`HERMES.md`、`.cursorrules` 等开发者上下文文件加入 `MemoryMiddleware.sources` 或 `SuperAgent` stable prompt 自动加载列表。

#### Scenario: 仓库 AGENTS 不进入模型

- **WHEN** 任意用户运行 `SuperAgent`
- **THEN** 注入的 `<agent_memory>` **SHALL NOT** 包含仓库级 Git 分支流程等开发者规范正文

### Requirement: AIO 沙箱可访问用户记忆文件

当 `SuperAgent` 使用 AIO 沙箱时，容器 rw mount 的 `users/{uid}/` **SHALL** 包含 `AGENTS.md` 与 `USER.md`（位于 mount 根下，非 `sessions/{sid}/workspace` 内）。`MemoryMiddleware` 经 virtual `/memory/` 读写 **SHALL** 与宿主机路径一致。

#### Scenario: 容器内记忆路径

- **WHEN** AIO 容器已 mount `users/{uid}/` → `/workspace`
- **THEN** `AGENTS.md` 在容器内路径 SHALL 为 `/workspace/AGENTS.md`，与 virtual `/memory/AGENTS.md` 映射一致
