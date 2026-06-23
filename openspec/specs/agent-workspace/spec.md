## Purpose

本能力规定 Noesis **Agent 会话工作区**的磁盘布局、路径解析、鉴权边界与生命周期：为 `DeepResearchAgent`、`FaultOperationAgent` 等提供按 **用户 + 会话（session_id）** 隔离的可写目录；固定根目录为仓库根下 **`.data/agent_workspace/`**（见 `common.paths.DATA_DIR`）；与只读 `extensions/skills` 及 **`.data/chat_attachments/`** 会话附件职责分离。

## 路径命名说明

| 路径 | 含义 |
|------|------|
| `{REPO_ROOT}/.data/` | 本地运行时数据根目录（**以 `.` 开头**，gitignore） |
| `{REPO_ROOT}/.data/agent_workspace/` | Agent 工作区根（子目录名 **无** 前导 `.`） |
| `{REPO_ROOT}/.data/agent_workspace/users/{user_id}/sessions/{session_id}/workspace/` | 单次会话可写 backend 根 |

**SHALL NOT** 与遗留路径 `backend/.agent_workspace`（点在 `agent_workspace` 上、位于 `backend/` 下）混淆；本能力不使用仓库根级的 `.agent_workspace` 目录。
## Requirements
### Requirement: 工作区根 SHALL 固定为 DATA_DIR 下的 agent_workspace

路径模块 SHALL 通过 `user_data_paths.get_workspace_dir` 将会话工作区定义为：

`{REPO_ROOT}/.data/users/{user_id}/sessions/{session_id}/workspace/`

`config/agent_workspace_paths.py` **MAY** 保留为薄封装，但权威路径 **SHALL** 来自 `user-data-layout`。

系统 **SHALL NOT** 再向 `.data/agent_workspace/` 写入新数据；**SHALL NOT** 提供 yaml、环境变量覆盖工作区根路径。

#### Scenario: 解析会话工作区完整路径

- **WHEN** 调用 `get_workspace_dir("42", "sess-abc")`
- **THEN** 返回路径 SHALL 等于 `{REPO_ROOT}/.data/users/42/sessions/sess-abc/workspace`

#### Scenario: 不使用遗留 backend 全局目录

- **WHEN** 任意 Agent 挂载可写 backend
- **THEN** **SHALL NOT** 写入 `backend/.agent_workspace` 或 `backend/.agent_workspace/fault_ops`

#### Scenario: 不使用已废弃的 agent_workspace 根

- **WHEN** 新会话首次写入 workspace
- **THEN** 文件 **SHALL** 落在 `.data/users/{uid}/sessions/{sid}/workspace/`，**SHALL NOT** 落在 `.data/agent_workspace/`

### Requirement: 系统 SHALL 提供集中式会话工作区路径解析

模块 SHALL 提供（可直接或经 `agent_workspace_paths` 委托）：

- `get_workspace_dir(user_id, session_id)`
- `ensure_workspace_dir(user_id, session_id)`
- `delete_session_workspace(user_id, session_id)`（幂等，删除 `sessions/{session_id}/` 整树或仅 `workspace/` 由 `delete_session_data` 统一处理）

`user_id` 与 `session_id` 拼入路径前 SHALL 校验仅含 `[A-Za-z0-9_-]`，非法值 SHALL 抛出 `ValueError`。

#### Scenario: 合法会话创建工作区

- **WHEN** 调用 `ensure_workspace_dir("42", "sess-abc-123")` 且 `.data/` 可写
- **THEN** SHALL 创建 `.data/users/42/sessions/sess-abc-123/workspace/` 并返回绝对路径

#### Scenario: 非法 session_id 拒绝

- **WHEN** `session_id` 含 `..` 或 `/`
- **THEN** SHALL 抛出 `ValueError`，**SHALL NOT** 在 `.data/users` 外创建目录

#### Scenario: 删除会话工作区幂等

- **WHEN** 对不存在的 `user_id`/`session_id` 调用 `delete_session_workspace`
- **THEN** SHALL 正常返回，**SHALL NOT** 抛出文件不存在异常

### Requirement: Agent 可写 backend SHALL 绑定 user_id 与 session_id

当 `run_agent` 收到有效 `session_id` 与 `current_user` 时，系统 SHALL `ensure_workspace_dir` 并经 **user 级 AIO 沙箱** + **当前 session virtual `/`** 访问。

#### Scenario: 两会话并行写入不冲突

- **WHEN** 同用户 session `s1` 与 `s2` 各自 filesystem 写入 `/notes.md`
- **THEN** SHALL 分别落在 `sessions/s1/workspace/notes.md` 与 `sessions/s2/workspace/notes.md`；**MAY** 共用 **一个** AIO 容器

#### Scenario: 不同用户隔离

- **WHEN** 用户 `u1` 与 `u2` 均有 `session_id=abc`
- **THEN** 工作区路径与 AIO 容器 **SHALL** 均隔离

### Requirement: SummarizationOffloadMiddleware 卸载 SHALL 落在会话工作区内

超大 tool 结果卸载 SHALL 写入当前会话 backend 根下 `summary_offload/`。

#### Scenario: 卸载路径随会话 backend

- **WHEN** 会话 `s1` 触发 tool 结果卸载
- **THEN** 完整内容 SHALL 在 `.data/users/{uid}/sessions/s1/workspace/summary_offload/` 下

### Requirement: 会话软删 SHALL 同步删除 Agent 工作区

软删 session 前 **SHALL** cancel 进行中 Agent run；**SHALL** `delete_session_workspace`；**SHALL NOT** `destroy_user_sandbox`。

#### Scenario: 删 session 前先停止 Agent

- **WHEN** 用户删除进行中的会话 `s1`
- **THEN** SHALL 先 cancel，**SHALL NOT** 在 Agent 仍写 workspace 时 `rmtree`

#### Scenario: 删 session 保留用户沙箱

- **WHEN** 用户删除 session `s1` 且仍有 `s2`
- **THEN** `sessions/s1/` **SHALL** 不存在；`u1` AIO 容器 **MAY** 继续运行

### Requirement: Agent 工作区与 skills-filesystem、chat-session-attachments 边界

系统 SHALL 维持下表所列 Agent 工作区、Skills 与聊天附件三者的职责分离；Agent **SHALL NOT** 将附件目录作为默认可写 backend 根。
| 维度 | `agent-workspace` | `skills-filesystem` | `chat-session-attachments` |
|------|-------------------|---------------------|----------------------------|
| 消费方 | `FilesystemMiddleware` Agent | Skills 管理 API + Agent `/skills/`、`/user-skills/` | `GeneralQAAgent` 附件 |
| 路径 | `.data/users/{uid}/sessions/{sid}/workspace/` | 平台：`extensions/skills`；用户：`.data/users/{uid}/skills/` | `.data/users/{uid}/sessions/{sid}/uploads|attachments/` |
| 写入 | Agent 笔记/卸载 | 用户 ZIP → `skills/`；平台只读 | 用户上传 |
| 隔离 | user + session | 平台全局 + user | user + session |

Agent **SHALL NOT** 将附件目录作为 `LocalShellBackend` 默认可写根。

#### Scenario: Skills 仍为全局只读

- **WHEN** 深度研究 Agent 写入 `/skills/foo.md`
- **THEN** 可写变更 **SHALL** 仅落在会话 `workspace/` 默认盘

#### Scenario: 用户 skills 目录只读挂载

- **WHEN** Agent 写入 `/user-skills/foo.md`
- **THEN** **SHALL NOT** 修改 `.data/users/{uid}/skills/` 下文件（写入应落在 workspace 默认盘）

### Requirement: 沙箱 rw 挂载 SHALL 为 users/{user_id} 根

runner **SHALL** 将宿主机 `.data/agent_workspace/users/{user_id}/` rw mount 至 AIO `/workspace`；**SHALL** ro mount `extensions/skills` → `/skills`。

#### Scenario: 附件不在沙箱内

- **WHEN** Agent 经 AIO 访问 `.data/chat_attachments/`
- **THEN** **SHALL NOT** 读到（除非 GeneralQA 附件工具）

