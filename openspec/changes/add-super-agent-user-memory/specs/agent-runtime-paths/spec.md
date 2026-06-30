## ADDED Requirements

### Requirement: 用户记忆文件路径 API

路径模块（`config/user_data_paths.py`）SHALL 除现有 `skills/` 与会话子树外，支持用户级记忆文件：

| API | 返回路径 |
|-----|----------|
| `get_user_agents_md_path(user_id)` | `{DATA_DIR}/users/{user_id}/AGENTS.md` |
| `get_user_profile_md_path(user_id)` | `{DATA_DIR}/users/{user_id}/USER.md` |
| `ensure_user_memory_files(user_id)` | 创建用户根（若不存在）并 seed 上述文件（若不存在） |

用户记忆文件 **SHALL** 位于 `users/{user_id}/` 根下，**SHALL NOT** 位于 `sessions/{session_id}/workspace/` 内。

#### Scenario: ensure 创建 seed 文件

- **WHEN** 对新用户调用 `ensure_user_memory_files("99")`
- **THEN** SHALL 创建 `.data/users/99/AGENTS.md` 且文件非空（含 seed 注释或占位节）

#### Scenario: 记忆路径不在 workspace 下

- **WHEN** 调用 `get_user_agents_md_path("42")` 与 `get_workspace_dir("42", "s1")`
- **THEN** `AGENTS.md` 路径 **SHALL NOT** 以 `sessions/s1/workspace` 为父目录

## MODIFIED Requirements

### Requirement: 会话子树布局 SHALL 统一

对每个合法 `(user_id, session_id)`，系统 SHALL 使用下列路径（不存在时在首次写入时创建）：

| 用途 | 相对路径（于 `users/{user_id}/`） |
|------|----------------------------------|
| 用户记忆（Agent 惯例） | `AGENTS.md` |
| 用户画像 | `USER.md` |
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

#### Scenario: 解析用户 AGENTS.md

- **WHEN** 调用 `get_user_agents_md_path("42")`
- **THEN** 返回路径 SHALL 等于 `{REPO_ROOT}/.data/users/42/AGENTS.md`

### Requirement: 工作区、Skills 与聊天附件边界 SHALL 职责分离

系统 SHALL 维持下表所列四者的职责分离：

| 维度 | 会话工作区 | 用户记忆 | `skills-filesystem` | `chat-session-attachments` |
|------|-----------|----------|---------------------|----------------------------|
| 消费方 | `FilesystemMiddleware` Agent | `MemoryMiddleware` + `/memory/` | Skills API + Agent `/skills/` | `GeneralQAAgent` 附件 |
| 路径 | `sessions/{sid}/workspace/` | `users/{uid}/AGENTS.md`、`USER.md` | 平台 + `users/{uid}/skills/` | `sessions/{sid}/uploads\|attachments/` |
| 写入 | Agent 任务产物 | Agent 写 `AGENTS.md`；`USER.md` 仅 API/运维 | 用户 ZIP → `skills/` | 用户上传 |
| 隔离 | user + session | user | 平台 + user | user + session |
| 删 session | 删除 workspace 子树 | **保留** | **保留** | 删除附件子树 |

Agent **SHALL NOT** 将附件目录作为默认可写根；**SHALL NOT** 将用户记忆存入 workspace 以规避删会话清理。

#### Scenario: Skills 仍为只读挂载

- **WHEN** Agent 写入 `/skills/extensions/foo.md`
- **THEN** 操作 SHALL 失败或拒绝，**SHALL NOT** 修改平台 skills

#### Scenario: 删 session 不删 AGENTS.md

- **WHEN** `delete_session_data(uid, sid)` 成功
- **THEN** `users/{uid}/AGENTS.md` SHALL 仍存在
