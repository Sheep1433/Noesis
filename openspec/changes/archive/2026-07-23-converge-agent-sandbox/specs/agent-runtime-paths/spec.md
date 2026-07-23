## Purpose

将运行时路径权威表与收敛后的沙箱挂载对齐：session workspace = 容器 `/workspace`；Skills = `/skills/public|personal`；记忆不进 Shell 默认挂载面。

## MODIFIED Requirements

### Requirement: 工作区、Skills 与聊天附件边界 SHALL 职责分离

系统 SHALL 维持下表所列四者的职责分离：

| 维度 | 会话工作区 | 用户记忆 | `skills-filesystem` | `chat-session-attachments` |
|------|-----------|----------|---------------------|----------------------------|
| 消费方 | `FilesystemMiddleware` Agent | `MemoryMiddleware` + 面板 API | Skills API + Agent `/skills/public\|personal` | `GeneralQAAgent` 附件 |
| 路径 | `sessions/{sid}/workspace/` | `users/{uid}/AGENTS.md`、`USER.md` | 平台 + `users/{uid}/skills/` | `sessions/{sid}/uploads\|attachments/` |
| 写入 | Agent 任务产物 | Agent / 用户面板 | 用户 ZIP → `skills/` | 用户上传 |
| 隔离 | user + session | user | 平台 + user | user + session |
| 删 session | 删除 workspace 子树 | **保留** | **保留** | 删除附件子树 |
| 沙箱可见 | `/workspace` rw | **默认不可见** | `/skills/public`、`/skills/personal` ro | **默认不可见** |

Agent **SHALL NOT** 将附件目录作为默认可写根；**SHALL NOT** 将用户记忆存入 workspace 以规避删会话清理；**SHALL NOT** 依赖 Shell 访问记忆或其它 session。

#### Scenario: 删 session 不删 AGENTS.md

- **WHEN** `delete_session_data(uid, sid)` 成功
- **THEN** `users/{uid}/AGENTS.md` SHALL 仍存在

#### Scenario: 公共 Skills 只读

- **WHEN** 超级 Agent `write_file` 至 `/skills/public/foo.md`
- **THEN** **SHALL NOT** 修改 `extensions/skills` 或容器 `/skills/public`

#### Scenario: 个人 skills 目录 Agent 只读

- **WHEN** Agent `write_file` 至 `/skills/personal/foo/SKILL.md`
- **THEN** **SHALL NOT** 修改 `.data/users/{uid}/skills/` 下文件

#### Scenario: research 子目录仅用于调研类产物

- **WHEN** Agent 在深度调研等 research 场景 `write_file` 至 `/research/notes.md` 或 `research/notes.md`
- **THEN** 变更 **SHALL** 落在 `sessions/{sid}/workspace/research/notes.md`

#### Scenario: 通用任务默认写入 workspace 根

- **WHEN** 通用智能体 `write_file` 至 `/diagram.mmd`
- **THEN** 变更 **SHALL** 落在 `sessions/{sid}/workspace/diagram.mmd`

#### Scenario: filesystem 与相对路径 execute 一致

- **WHEN** 当前 run 为 session `s1`，Agent `write_file("/notes.md")` 后 `execute("cat notes.md")`（cwd `/workspace`）
- **THEN** SHALL 读取同一 `sessions/s1/workspace/notes.md`

### Requirement: 沙箱 rw 挂载 SHALL 为 users/{user_id} 根

runner **SHALL** 将宿主机当前 session workspace rw mount 至容器 `/workspace`；**SHALL** ro mount 公共 skills → `/skills/public`、个人 skills → `/skills/personal`（详见 `agent-sandbox`、`container-deployment`）。

**SHALL NOT** 将整个 `users/{user_id}/` rw 挂载为容器根工作区。

#### Scenario: 附件不经沙箱默认盘暴露

- **WHEN** Agent 经沙箱 filesystem / execute 访问路径
- **THEN** 默认 **SHALL NOT** 将 `uploads/`、`attachments/` 作为可写根；附件消费 **SHALL** 经 `chat-session-attachments` 工具链

### Requirement: Agent 虚拟路径表 SHALL 双通道一致

系统 **SHALL** 维持下表；**filesystem 工具** **SHALL** 将虚拟路径解析至对应物理位置。**`execute` SHALL NOT** 依赖会破坏 Shell 语法的整命令路径 rewrite；Shell 侧使用容器真实路径或相对路径。

| Agent 路径（filesystem） | 容器路径 | local_shell host | 读写 |
|--------------------------|----------|------------------|------|
| `/notes.md` 等 workspace 根 | `/workspace/notes.md` | `.data/users/{uid}/sessions/{sid}/workspace/notes.md` | 可写 |
| `/research/...` 或 `research/...` | `/workspace/research/...` | 同布局 `research/...` | 可写 |
| `/skills/public/...` | `/skills/public/...` | `extensions/skills/...` | 只读 |
| `/skills/personal/...` | `/skills/personal/...` | `.data/users/{uid}/skills/...` | 只读 |
| `/memory/AGENTS.md`、`/memory/USER.md` | （可不挂载） | `.data/users/{uid}/...` | 经 Memory 通道可写 |

常量 **SHALL** 集中在 `mount_paths.py`；权威 Skills 路径为 `/skills/public` 与 `/skills/personal`。过渡期 **MAY** 将 `/skills/extensions`、`/skills/custom`、`/user-skills` 在 **filesystem 层** 别名到新路径，**SHALL NOT** 在 Shell rewrite 中恢复旧绝对路径表。

#### Scenario: write 与相对路径 execute 同一文件

- **WHEN** session `s1` 下 Agent `write_file("/out.txt", "ok")` 后 `execute("cat out.txt")`
- **THEN** execute 输出 **SHALL** 包含 `ok`

#### Scenario: workspace 根双通道（工具）

- **WHEN** Agent `write_file("/notes.md", "n")` 后经 filesystem `read_file("/notes.md")`
- **THEN** **SHALL** 读到 `n`

#### Scenario: memory 不经 Shell 规范路径

- **WHEN** Agent 仅通过 `execute` 尝试读取用户记忆文件
- **THEN** 系统 **SHALL NOT** 将「Shell 可读 `/memory/...`」作为规范能力；记忆 **SHALL** 经 MemoryMiddleware / API
