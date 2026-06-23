## MODIFIED Requirements

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

当 `DeepResearchAgent` 或 `FaultOperationAgent` 的 `run_agent` 收到有效 `session_id` 与 `current_user` 时，系统 SHALL 调用 `ensure_workspace_dir` 并以 `.data/users/{user_id}/sessions/{session_id}/workspace/` 为 `LocalShellBackend` 根。

当 `session_id` 或 `user_id` 缺失时，**SHALL NOT** 回退全局可写目录；**SHALL** 记录 warning 并以无 backend 或明确错误结束该轮。

#### Scenario: 两会话并行写入不冲突

- **WHEN** 同用户会话 `s1` 与 `s2` 并行深度研究，各自写入 `/notes.md`
- **THEN** SHALL 存在 `.data/users/{uid}/sessions/s1/workspace/notes.md` 与 `.../s2/workspace/notes.md`，内容互不覆盖

#### Scenario: 不同用户同 session_id 字符串隔离

- **WHEN** 用户 `u1` 与 `u2` 均有 `session_id=abc`
- **THEN** 工作区 SHALL 分别为 `.data/users/u1/sessions/abc/workspace/` 与 `.../users/u2/sessions/abc/workspace/`

### Requirement: SummarizationOffloadMiddleware 卸载 SHALL 落在会话工作区内

超大 tool 结果卸载 SHALL 写入当前会话 backend 根下 `summary_offload/`。

#### Scenario: 卸载路径随会话 backend

- **WHEN** 会话 `s1` 触发 tool 结果卸载
- **THEN** 完整内容 SHALL 在 `.data/users/{uid}/sessions/s1/workspace/summary_offload/` 下

### Requirement: 会话软删 SHALL 同步删除 Agent 工作区

用户软删本人会话时，Service SHALL 在 DB 软删成功后调用 `delete_session_data(user_id, session_id)`（或等价函数），删除 `.data/users/{user_id}/sessions/{session_id}/` 整棵子树（含 workspace、uploads、attachments）。

本 Requirement **SHALL NOT** 替代附件 TTL 对未删会话的 lazy 清理；**SHALL NOT** 提供关闭磁盘清理的配置开关。

#### Scenario: 删会话清工作区

- **WHEN** 用户删除本人会话 `s1`
- **THEN** `.data/users/{uid}/sessions/s1/` SHALL 不再存在

### Requirement: Agent 工作区与 skills-filesystem、chat-session-attachments 边界

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
