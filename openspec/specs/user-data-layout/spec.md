# user-data-layout Specification

## Purpose
TBD - created by archiving change unify-user-data-layout. Update Purpose after archive.
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

| 用途 | 相对路径 |
|------|----------|
| 用户 Skills（跨会话） | `skills/` |
| 会话根 | `sessions/{session_id}/` |
| Agent 工作区 | `sessions/{session_id}/workspace/` |
| 附件原文件 | `sessions/{session_id}/uploads/` |
| 附件 Markdown 副本 | `sessions/{session_id}/attachments/` |

平台 Skills **SHALL NOT** 存放于 `.data/users/` 下。

#### Scenario: 解析会话工作区

- **WHEN** 调用 `get_workspace_dir("42", "sess-abc")`
- **THEN** 返回路径 SHALL 等于 `{REPO_ROOT}/.data/users/42/sessions/sess-abc/workspace`

#### Scenario: 解析用户 Skills 目录

- **WHEN** 调用 `get_user_skills_dir("42")`
- **THEN** 返回路径 SHALL 等于 `{REPO_ROOT}/.data/users/42/skills`

### Requirement: 系统 SHALL 提供会话数据删除 API

模块 SHALL 提供 `delete_session_data(user_id, session_id)`，删除 `{REPO_ROOT}/.data/users/{user_id}/sessions/{session_id}/` 整棵子树（幂等）。

该函数 **SHALL NOT** 删除 `skills/` 或其它会话目录。

#### Scenario: 删会话幂等

- **WHEN** 对不存在的 `sessions/{session_id}/` 调用 `delete_session_data`
- **THEN** SHALL 正常返回，**SHALL NOT** 抛出文件不存在异常

#### Scenario: 删会话保留用户 Skills

- **WHEN** 用户删除会话 `s1` 且 `.data/users/{uid}/skills/` 非空
- **THEN** `skills/` 目录及内容 SHALL 保持不变

### Requirement: 系统 SHALL 提供遗留布局迁移脚本

仓库 SHALL 提供 `scripts/migrate_user_data_layout.py`，支持：

- 自 `.data/agent_workspace/users/{uid}/sessions/{sid}/` 迁移至 `.data/users/{uid}/sessions/{sid}/workspace/`；
- 自 `.data/chat_attachments/sessions/{sid}/` 迁移至 `.data/users/{uid}/sessions/{sid}/`（`user_id` 来自 `t_chat_attachment` 或 `t_chat_session`）；
- 自 `.data/user_skills/users/{uid}/` 迁移至 `.data/users/{uid}/skills/`；
- `--dry-run` 仅打印计划而不写入。

#### Scenario: dry-run 不修改磁盘

- **WHEN** 运维执行迁移脚本并传入 `--dry-run`
- **THEN** SHALL 输出拟迁移项列表且 **SHALL NOT** 创建或移动目标文件

