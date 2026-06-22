## MODIFIED Requirements

### Requirement: Agent 可写 backend SHALL 绑定 user_id 与 session_id

当 `run_agent` 收到有效 `session_id` 与 `current_user` 时，系统 SHALL `ensure_workspace_dir` 并经 **user 级 AIO 沙箱** + **当前 session virtual `/`** 访问。

#### Scenario: 两会话并行写入不冲突

- **WHEN** 同用户 session `s1` 与 `s2` 各自 filesystem 写入 `/notes.md`
- **THEN** SHALL 分别落在 `sessions/s1/workspace/notes.md` 与 `sessions/s2/workspace/notes.md`；**MAY** 共用 **一个** AIO 容器

#### Scenario: 不同用户隔离

- **WHEN** 用户 `u1` 与 `u2` 均有 `session_id=abc`
- **THEN** 工作区路径与 AIO 容器 **SHALL** 均隔离

### Requirement: 会话软删 SHALL 同步删除 Agent 工作区且保留用户沙箱

软删 session 前 **SHALL** cancel 进行中 Agent run；**SHALL** `delete_session_workspace`；**SHALL NOT** `destroy_user_sandbox`。

#### Scenario: 删 session 前先停止 Agent

- **WHEN** 用户删除进行中的会话 `s1`
- **THEN** SHALL 先 cancel，**SHALL NOT** 在 Agent 仍写 workspace 时 `rmtree`

#### Scenario: 删 session 保留用户沙箱

- **WHEN** 用户删除 session `s1` 且仍有 `s2`
- **THEN** `sessions/s1/` **SHALL** 不存在；`u1` AIO 容器 **MAY** 继续运行

## ADDED Requirements

### Requirement: 沙箱 rw 挂载 SHALL 为 users/{user_id} 根

runner **SHALL** 将宿主机 `.data/agent_workspace/users/{user_id}/` rw mount 至 AIO `/workspace`；**SHALL** ro mount `extensions/skills` → `/skills`。

#### Scenario: 附件不在沙箱内

- **WHEN** Agent 经 AIO 访问 `.data/chat_attachments/`
- **THEN** **SHALL NOT** 读到（除非 GeneralQA 附件工具）
