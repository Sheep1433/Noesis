# agent-runtime-paths Specification

## Purpose

本能力规定 Noesis **用户运行时数据**的统一磁盘布局与路径解析：权威根为 `{REPO_ROOT}/.data/users/{user_id}/`；会话子树含 Agent 工作区、聊天附件与跨会话用户 Skills；提供集中式路径 API、会话软删时的磁盘清理、遗留布局迁移，以及与 `agent-sandbox`、`skills-filesystem`、`chat-session-attachments` 的职责边界。

**合并来源**：原 `user-data-layout` 与 `agent-workspace`（2026-06 统一用户数据布局后二者语义重叠，合并为单一事实来源）。

## 路径命名说明

| 路径 | 含义 |
|------|------|
| `{REPO_ROOT}/.data/` | 本地运行时数据根（gitignore） |
| `{REPO_ROOT}/.data/users/{user_id}/` | 单用户数据根 |
| `{REPO_ROOT}/.data/users/{user_id}/sessions/{session_id}/workspace/` | 单次会话 Agent 可写 backend 根 |
| `{REPO_ROOT}/.data/users/{user_id}/sessions/{session_id}/uploads/` | 附件原文件 |
| `{REPO_ROOT}/.data/users/{user_id}/sessions/{session_id}/attachments/` | 附件 Markdown 副本 |
| `{REPO_ROOT}/.data/users/{user_id}/skills/` | 用户 Skills（跨会话） |

**已废弃、不得再写入新数据**：

- `{REPO_ROOT}/.data/agent_workspace/`（旧工作区根）
- `{REPO_ROOT}/.data/chat_attachments/`（旧附件根）
- `backend/.agent_workspace`（遗留开发路径）

## Requirements

### Requirement: 用户数据根 SHALL 固定为 DATA_DIR 下的 users

路径模块（`config/user_data_paths.py`）SHALL 将用户数据根定义为 `common.paths.DATA_DIR / "users"`，即 `{REPO_ROOT}/.data/users`。

单次用户根路径 SHALL 为 `{REPO_ROOT}/.data/users/{user_id}/`，其中 `user_id` 拼入路径前 SHALL 经 `validate_segment` 校验仅含 `[A-Za-z0-9_-]`。

系统 **SHALL NOT** 提供 yaml 或环境变量覆盖 `.data/users` 根路径。

#### Scenario: 解析用户根路径

- **WHEN** 调用 `get_user_root("42")`
- **THEN** 返回路径 SHALL 等于 `{REPO_ROOT}/.data/users/42`

#### Scenario: 非法 user_id 拒绝

- **WHEN** `user_id` 含 `..` 或 `/`
- **THEN** SHALL 抛出 `ValueError`，**SHALL NOT** 在 `.data/users` 外创建目录

### Requirement: 会话子树布局 SHALL 统一

对每个合法 `(user_id, session_id)`，系统 SHALL 使用下列路径（不存在时在首次写入时创建）：

| 用途 | 相对路径（于 `users/{user_id}/`） |
|------|----------------------------------|
| 用户 Skills（跨会话） | `skills/` |
| 会话根 | `sessions/{session_id}/` |
| Agent 工作区 | `sessions/{session_id}/workspace/` |
| 附件原文件 | `sessions/{session_id}/uploads/` |
| 附件 Markdown 副本 | `sessions/{session_id}/attachments/` |

平台 Skills **SHALL NOT** 存放于 `.data/users/` 下（平台 Skills 见 `skills-filesystem`）。

#### Scenario: 解析会话工作区

- **WHEN** 调用 `get_workspace_dir("42", "sess-abc")`
- **THEN** 返回路径 SHALL 等于 `{REPO_ROOT}/.data/users/42/sessions/sess-abc/workspace`

#### Scenario: 解析用户 Skills 目录

- **WHEN** 调用 `get_user_skills_dir("42")`
- **THEN** 返回路径 SHALL 等于 `{REPO_ROOT}/.data/users/42/skills`

### Requirement: 系统 SHALL NOT 再向遗留路径写入

新会话首次写入 workspace、附件或用户 Skills 时，文件 **SHALL** 落在 `.data/users/{uid}/...` 下，**SHALL NOT** 落在 `.data/agent_workspace/` 或 `.data/chat_attachments/`。

`config/agent_workspace_paths.py` **MAY** 保留为薄封装，但权威路径 **SHALL** 来自 `user_data_paths`。

#### Scenario: 不使用遗留 backend 全局目录

- **WHEN** 任意 Agent 挂载可写 backend
- **THEN** **SHALL NOT** 写入 `backend/.agent_workspace` 或 `backend/.agent_workspace/fault_ops`

### Requirement: 系统 SHALL 提供集中式路径与会话数据删除 API

模块 SHALL 提供（可直接或经 `agent_workspace_paths` 委托）：

- `get_workspace_dir(user_id, session_id)`
- `ensure_workspace_dir(user_id, session_id)`
- `delete_session_data(user_id, session_id)`：删除 `{REPO_ROOT}/.data/users/{user_id}/sessions/{session_id}/` 整棵子树（含 workspace、uploads、attachments；幂等）
- `delete_session_workspace(user_id, session_id)` **MAY** 保留为 `delete_session_data` 的别名或薄封装，供历史调用方兼容

`delete_session_data` **SHALL NOT** 删除 `skills/` 或其它会话目录。

`user_id` 与 `session_id` 拼入路径前 SHALL 校验仅含 `[A-Za-z0-9_-]`，非法值 SHALL 抛出 `ValueError`。

#### Scenario: 合法会话创建工作区

- **WHEN** 调用 `ensure_workspace_dir("42", "sess-abc-123")` 且 `.data/` 可写
- **THEN** SHALL 创建 `.data/users/42/sessions/sess-abc-123/workspace/` 并返回绝对路径

#### Scenario: 删会话幂等

- **WHEN** 对不存在的 `sessions/{session_id}/` 调用 `delete_session_data`
- **THEN** SHALL 正常返回，**SHALL NOT** 抛出文件不存在异常

#### Scenario: 删会话保留用户 Skills

- **WHEN** 用户删除会话 `s1` 且 `.data/users/{uid}/skills/` 非空
- **THEN** `skills/` 目录及内容 SHALL 保持不变

### Requirement: Agent 可写 backend SHALL 绑定 user_id 与 session_id

当 `run_agent` 收到有效 `session_id` 与 `current_user` 时，系统 SHALL `ensure_workspace_dir` 并经 **user 级 AIO 沙箱** + **当前 session virtual `/`** 访问（见 `agent-sandbox`）。

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

### Requirement: 会话软删 SHALL 同步删除会话子树且保留用户沙箱

软删 session 前 **SHALL** cancel 进行中 Agent run；**SHALL** 调用 `delete_session_data(user_id, session_id)`；**SHALL NOT** `destroy_user_sandbox`（除非产品另行规定——默认 **不** destroy）。

`ChatService` 删会话磁盘清理 **SHALL** 以 `delete_session_data` 为唯一入口（见 `platform-chat`）。

#### Scenario: 删 session 前先停止 Agent

- **WHEN** 用户删除进行中的会话 `s1`
- **THEN** SHALL 先 cancel，**SHALL NOT** 在 Agent 仍写 workspace 时 `rmtree`

#### Scenario: 删 session 保留用户沙箱

- **WHEN** 用户删除 session `s1` 且仍有 `s2`
- **THEN** `sessions/s1/` **SHALL** 不存在；`u1` AIO 容器 **MAY** 继续运行

### Requirement: 工作区、Skills 与聊天附件边界 SHALL 职责分离

系统 SHALL 维持下表所列三者的职责分离：

| 维度 | 会话工作区 | `skills-filesystem` | `chat-session-attachments` |
|------|-----------|---------------------|----------------------------|
| 消费方 | `FilesystemMiddleware` Agent | Skills API + Agent `/skills/`、`/user-skills/` | `GeneralQAAgent` 附件 |
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

runner **SHALL** 将宿主机 `{NOESIS_HOST_DATA_DIR}/users/{user_id}/` rw mount 至 AIO `/workspace`；**SHALL** ro mount `extensions/skills` → `/skills`（详见 `agent-sandbox`、`container-deployment`）。

#### Scenario: 附件不经 AIO 默认盘暴露

- **WHEN** Agent 经 AIO filesystem 工具访问路径
- **THEN** 默认 **SHALL NOT** 将 `uploads/`、`attachments/` 作为可写根；附件消费 **SHALL** 经 `chat-session-attachments` 工具链

### Requirement: 系统 SHALL 提供遗留布局迁移脚本

仓库 SHALL 提供 `scripts/migrate_user_data_layout.py`，支持：

- 自 `.data/agent_workspace/users/{uid}/sessions/{sid}/` 迁移至 `.data/users/{uid}/sessions/{sid}/workspace/`；
- 自 `.data/chat_attachments/sessions/{sid}/` 迁移至 `.data/users/{uid}/sessions/{sid}/`（`user_id` 来自 `t_chat_attachment` 或 `t_chat_session`）；
- 自 `.data/user_skills/users/{uid}/` 迁移至 `.data/users/{uid}/skills/`；
- `--dry-run` 仅打印计划而不写入。

#### Scenario: dry-run 不修改磁盘

- **WHEN** 运维执行迁移脚本并传入 `--dry-run`
- **THEN** SHALL 输出拟迁移项列表且 **SHALL NOT** 创建或移动目标文件
