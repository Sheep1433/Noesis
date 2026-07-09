# agent-runtime-paths Specification

## Purpose

本能力规定 Noesis **用户运行时数据**的统一磁盘布局与路径解析：权威根为 `{REPO_ROOT}/.data/users/{user_id}/`；会话子树含 Agent 工作区、聊天附件、用户记忆与跨会话用户 Skills；提供集中式路径 API、会话软删时的磁盘清理，以及与 `agent-sandbox`、`skills-filesystem`、`chat-session-attachments` 的职责边界。

## 路径命名说明

| 路径 | 含义 |
|------|------|
| `{REPO_ROOT}/.data/` | 本地运行时数据根（gitignore） |
| `{REPO_ROOT}/.data/users/{user_id}/` | 单用户数据根 |
| `{REPO_ROOT}/.data/users/{user_id}/sessions/{session_id}/workspace/` | 单次会话 Agent 可写 backend 根 |
| `{REPO_ROOT}/.data/users/{user_id}/sessions/{session_id}/uploads/` | 附件原文件 |
| `{REPO_ROOT}/.data/users/{user_id}/sessions/{session_id}/attachments/` | 附件 Markdown 副本 |
| `{REPO_ROOT}/.data/users/{user_id}/skills/` | 用户 Skills（跨会话） |
| `{REPO_ROOT}/.data/users/{user_id}/AGENTS.md` | 用户 Agent 记忆（跨会话） |
| `{REPO_ROOT}/.data/users/{user_id}/USER.md` | 用户画像（跨会话） |
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

### Requirement: 用户记忆文件路径 API

路径模块 **SHALL** 除现有 `skills/` 与会话子树外，支持用户级记忆文件：

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

### Requirement: 系统 SHALL 提供集中式路径与会话数据删除 API

路径权威模块为 `config/user_data_paths.py`，SHALL 提供：

- `get_workspace_dir(user_id, session_id)`
- `ensure_workspace_dir(user_id, session_id)`
- `delete_session_data(user_id, session_id)`：删除 `{REPO_ROOT}/.data/users/{user_id}/sessions/{session_id}/` 整棵子树（含 workspace、uploads、attachments；幂等）
- `delete_session_workspace(user_id, session_id)` **MAY** 保留为 `delete_session_data` 的别名或薄封装，供历史调用方兼容

`delete_session_data` **SHALL NOT** 删除 `skills/`、`AGENTS.md`、`USER.md` 或其它会话目录。

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

系统 SHALL 维持下表所列四者的职责分离：

| 维度 | 会话工作区 | 用户记忆 | `skills-filesystem` | `chat-session-attachments` |
|------|-----------|----------|---------------------|----------------------------|
| 消费方 | `FilesystemMiddleware` Agent | `MemoryMiddleware` + `/memory/`；面板 PUT API | Skills API + Agent `/skills/` | `GeneralQAAgent` 附件 |
| 路径 | `sessions/{sid}/workspace/` | `users/{uid}/AGENTS.md`、`USER.md` | 平台 + `users/{uid}/skills/` | `sessions/{sid}/uploads\|attachments/` |
| 写入 | Agent 任务产物 | Agent / 用户面板写 `AGENTS.md`、`USER.md` | 用户 ZIP → `skills/` | 用户上传 |
| 隔离 | user + session | user | 平台 + user | user + session |
| 删 session | 删除 workspace 子树 | **保留** | **保留** | 删除附件子树 |

Agent **SHALL NOT** 将附件目录作为默认可写根；**SHALL NOT** 将用户记忆存入 workspace 以规避删会话清理。

#### Scenario: 删 session 不删 AGENTS.md

- **WHEN** `delete_session_data(uid, sid)` 成功
- **THEN** `users/{uid}/AGENTS.md` SHALL 仍存在

#### Scenario: Skills 仍为全局只读

- **WHEN** 超级 Agent `write_file` 至 `/skills/extensions/foo.md`
- **THEN** **SHALL NOT** 修改 `extensions/skills` 或容器 `/skills/`

#### Scenario: 用户 skills 目录 Agent 只读

- **WHEN** Agent `write_file` 至 `/skills/custom/foo/SKILL.md`
- **THEN** **SHALL NOT** 修改 `.data/users/{uid}/skills/` 下文件

#### Scenario: research 子目录仅用于调研类产物

- **WHEN** Agent 在深度调研等 research 场景 `write_file` 至 `/research/notes.md`
- **THEN** 变更 **SHALL** 落在 `sessions/{sid}/workspace/research/notes.md`，**SHALL NOT** 写入其它 session 或 `skills/`

#### Scenario: 通用任务默认写入 workspace 根

- **WHEN** 通用智能体 `write_file` 至 `/diagram.mmd`（非 research 场景）
- **THEN** 变更 **SHALL** 落在 `sessions/{sid}/workspace/diagram.mmd`，**SHALL NOT** 默认写入 `research/` 子目录

#### Scenario: filesystem 默认盘写入 workspace 根

- **WHEN** 当前 run 为 session `s1`，Agent 经 filesystem 工具 `write_file("/notes.md")`
- **THEN** SHALL 写入 `sessions/s1/workspace/notes.md`，且 `execute("cat /notes.md")` **SHALL** 读取同一文件

### Requirement: 沙箱 rw 挂载 SHALL 为 users/{user_id} 根

runner **SHALL** 将宿主机 `{NOESIS_HOST_DATA_DIR}/users/{user_id}/` rw mount 至 AIO `/workspace`；**SHALL** ro mount `extensions/skills` → `/skills`（详见 `agent-sandbox`、`container-deployment`）。

#### Scenario: 附件不经 AIO 默认盘暴露

- **WHEN** Agent 经 AIO filesystem 工具访问路径
- **THEN** 默认 **SHALL NOT** 将 `uploads/`、`attachments/` 作为可写根；附件消费 **SHALL** 经 `chat-session-attachments` 工具链

### Requirement: Agent 虚拟路径表 SHALL 双通道一致

系统 **SHALL** 维持下表；**filesystem 工具**与 **`execute`**（经 workspace `PrefixBackend` 单点 rewrite）**SHALL** 将虚拟绝对路径解析至同一物理位置。

| Agent 虚拟路径 | AIO 容器 | local_shell host | 读写 |
|----------------|----------|------------------|------|
| `/research/...` | `/workspace/sessions/{sid}/workspace/research/...` | `.data/users/{uid}/sessions/{sid}/workspace/research/...` | 可写 |
| `/notes.md` 等 workspace 根 | `/workspace/sessions/{sid}/workspace/notes.md` | 同布局 | 可写 |
| `/skills/extensions/...` | `/skills/...` | `extensions/skills/...` | 只读 |
| `/skills/custom/...` | `/workspace/skills/...` | `.data/users/{uid}/skills/...` | 只读 |
| `/memory/AGENTS.md` | `/workspace/AGENTS.md` | `.data/users/{uid}/AGENTS.md` | 可写 |
| `/memory/USER.md` | `/workspace/USER.md` | `.data/users/{uid}/USER.md` | 可写 |

常量 **SHALL** 集中在 `mount_paths.py` + `PathRewriteContext` 构造；**SHALL NOT** 使用单层 `/skills/{name}` 作为权威路径。

#### Scenario: write 与 execute 同一 research 文件

- **WHEN** session `s1` 下 Agent `write_file("/research/out.txt", "ok")` 后 `execute("cat /research/out.txt")`
- **THEN** execute 输出 **SHALL** 包含 `ok`

#### Scenario: workspace 根双通道

- **WHEN** Agent `write_file("/notes.md", "n")` 后 `execute("cat /notes.md")`
- **THEN** 两者 **SHALL** 访问 `sessions/{sid}/workspace/notes.md`

#### Scenario: memory 双通道

- **WHEN** `users/{uid}/AGENTS.md` 存在，Agent `read_file("/memory/AGENTS.md")` 与 `execute("head -1 /memory/AGENTS.md")`
- **THEN** 两者 **SHALL** 读取同一文件内容

