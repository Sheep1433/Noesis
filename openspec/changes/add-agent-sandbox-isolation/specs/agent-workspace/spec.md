## MODIFIED Requirements

### Requirement: Agent 可写 backend SHALL 绑定 user_id 与 session_id

当 `run_agent` 收到有效 `session_id` 与 `current_user` 时，系统 SHALL `ensure_workspace_dir` 并经 **user 级**沙箱 + **session virtual `/`** 访问（见 `agent-sandbox`）。

#### Scenario: 两会话并行写入不冲突

- **WHEN** 同用户 session `s1` 与 `s2` 各自写入 `/notes.md`
- **THEN** SHALL 分别落在 `.../sessions/s1/workspace/notes.md` 与 `.../sessions/s2/workspace/notes.md`；**MAY** 共用同一用户沙箱容器

#### Scenario: 不同用户隔离

- **WHEN** 用户 `u1` 与 `u2` 均有 `session_id=abc`
- **THEN** 工作区路径与沙箱容器 **SHALL** 均隔离

### Requirement: 会话软删 SHALL 同步删除 Agent 工作区

软删 session 前，Service **SHALL** 对该 `session_id` 调用 `DeepResearchAgent.cancel_task` 与/或 `FaultOperationAgent.cancel_task`（若 qa_type 适用），**SHALL** 等待进行中的 Agent run 结束或标记取消后，再调用 `delete_session_workspace`。

软删 **SHALL NOT** 调用 `destroy_user_sandbox`。

#### Scenario: 删 session 前先停止 Agent

- **WHEN** 用户删除进行中的深度研究会话 `s1`
- **THEN** 系统 SHALL 先 cancel 对应流式任务，**SHALL NOT** 在 Agent 仍写 workspace 时 `rmtree` 会话目录

#### Scenario: 删 session 仅清磁盘

- **WHEN** 用户删除已 idle 的会话 `s1`
- **THEN** `.data/agent_workspace/users/{uid}/sessions/s1/` SHALL 不存在；用户沙箱 **MAY** 继续运行

## ADDED Requirements

### Requirement: 沙箱 rw 挂载 SHALL 为用户 agent_workspace 根

宿主机 `.data/agent_workspace/users/{user_id}/` 映射至用户沙箱 `/workspace`；Agent 仅通过 virtual `/` 与 exec 隔离访问当前 session 子树；`extensions/skills` **SHALL** ro 挂载 `/skills`。

#### Scenario: 附件目录不在沙箱内

- **WHEN** Agent 经沙箱访问 `.data/chat_attachments/`
- **THEN** **SHALL NOT** 读到附件（除非 GeneralQA 附件工具）
