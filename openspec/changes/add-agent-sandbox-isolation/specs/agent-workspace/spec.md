## MODIFIED Requirements

### Requirement: Agent 可写 backend SHALL 绑定 user_id 与 session_id

当 `run_agent` 收到有效 `session_id` 与 `current_user` 时，系统 SHALL `ensure_workspace_dir` 并经 **session 级 AIO 沙箱** + **session virtual `/`** 访问（见 `agent-sandbox`）。

#### Scenario: 两会话并行写入不冲突

- **WHEN** 同用户 session `s1` 与 `s2` 各自写入 `/notes.md`
- **THEN** SHALL 分别落在 `.../sessions/s1/workspace/notes.md` 与 `.../sessions/s2/workspace/notes.md`；**SHALL** 使用 **两个** AIO 容器

#### Scenario: 不同用户隔离

- **WHEN** 用户 `u1` 与 `u2` 均有 `session_id=abc`
- **THEN** 工作区路径与 AIO 容器 **SHALL** 均隔离

### Requirement: 会话软删 SHALL 同步删除 Agent 工作区与 session 沙箱

软删 session 前，Service **SHALL** 对该 `session_id` 调用 `DeepResearchAgent.cancel_task` 与/或 `FaultOperationAgent.cancel_task`（若 qa_type 适用），**SHALL** 等待进行中 Agent run 结束或标记取消后，再调用 `destroy_session_sandbox(user_id, session_id)`，再 `delete_session_workspace`。

#### Scenario: 删 session 前先停止 Agent

- **WHEN** 用户删除进行中的深度研究会话 `s1`
- **THEN** 系统 SHALL 先 cancel 对应流式任务，**SHALL NOT** 在 Agent 仍写 workspace 时 `rmtree` 会话目录

#### Scenario: 删 session 销毁 AIO 容器

- **WHEN** 用户删除会话 `s1`
- **THEN** `sessions/s1/` 磁盘子树 **SHALL** 不存在；`s1` 的 AIO 容器 **SHALL** 被 destroy

## ADDED Requirements

### Requirement: 沙箱 mount SHALL 仅为当前 session workspace

宿主机 `agent_workspace/users/{uid}/sessions/{sid}/workspace` → AIO `/workspace`；`extensions/skills` → `/skills`（ro）。**SHALL NOT** mount 整棵 `users/{uid}/`。

#### Scenario: 附件目录不在沙箱内

- **WHEN** Agent 经 AIO 沙箱访问 `.data/chat_attachments/`
- **THEN** **SHALL NOT** 读到附件（除非 GeneralQA 附件工具）
