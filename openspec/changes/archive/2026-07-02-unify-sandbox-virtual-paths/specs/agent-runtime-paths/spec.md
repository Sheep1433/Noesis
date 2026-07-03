## ADDED Requirements

### Requirement: Agent 虚拟路径表 SHALL 双通道一致

系统 **SHALL** 维持下表；**filesystem 工具**与 **`execute`**（经 workspace `PrefixBackend` 单点 rewrite）**SHALL** 将虚拟绝对路径解析至同一物理位置。

| Agent 虚拟路径 | AIO 容器 | local_shell host | 读写 |
|----------------|----------|------------------|------|
| `/research/...` | `/workspace/sessions/{sid}/workspace/research/...` | `.data/users/{uid}/sessions/{sid}/workspace/research/...` | 可写 |
| `/notes.md` 等 workspace 根 | `/workspace/sessions/{sid}/workspace/notes.md` | 同布局 | 可写 |
| `/skills/extensions/...` | `/skills/...` | `extensions/skills/...` | 只读 |
| `/skills/custom/...` | `/workspace/skills/...` | `.data/users/{uid}/skills/...` | 只读 |
| `/memory/AGENTS.md` | `/workspace/AGENTS.md` | `.data/users/{uid}/AGENTS.md` | 可写 |
| `/memory/USER.md` | `/workspace/USER.md` | `.data/users/{uid}/USER.md` | Agent 只读 |

常量 **SHALL** 集中在 `mount_paths.py` + `PathRewriteContext` 构造；**SHALL NOT** 使用单层 `/skills/{name}` 作为权威路径。

**协调**：`/memory/` 物理布局 **SHALL** 与 `add-super-agent-user-memory` 一致；本 change **SHALL** 先于该 change 归档。

#### Scenario: write 与 execute 同一 research 文件

- **WHEN** session `s1` 下 Agent `write_file("/research/out.txt", "ok")` 后 `execute("cat /research/out.txt")`
- **THEN** execute 输出 **SHALL** 包含 `ok`

#### Scenario: workspace 根双通道

- **WHEN** Agent `write_file("/notes.md", "n")` 后 `execute("cat /notes.md")`
- **THEN** 两者 **SHALL** 访问 `sessions/{sid}/workspace/notes.md`

#### Scenario: memory 双通道

- **WHEN** `users/{uid}/AGENTS.md` 存在，Agent `read_file("/memory/AGENTS.md")` 与 `execute("head -1 /memory/AGENTS.md")`
- **THEN** 两者 **SHALL** 读取同一文件内容

## MODIFIED Requirements

### Requirement: 工作区、Skills 与聊天附件边界 SHALL 职责分离

系统 SHALL 维持下表所列三者的职责分离：

| 维度 | 会话工作区 | `skills-filesystem` | `chat-session-attachments` |
|------|-----------|---------------------|----------------------------|
| 消费方 | `FilesystemMiddleware` Agent | Skills API + Agent `/skills/extensions/`、`/skills/custom/` | `GeneralQAAgent` 附件 |
| 路径 | `.data/users/{uid}/sessions/{sid}/workspace/`（任务产物常用 `research/` 子目录；根路径如 `notes.md` 亦合法） | 平台：`extensions/skills`；用户：`.data/users/{uid}/skills/` | `.data/users/{uid}/sessions/{sid}/uploads|attachments/` |
| 写入 | Agent 笔记/卸载 | 用户 ZIP → `skills/`；平台只读 | 用户上传 |
| 隔离 | user + session | 平台全局 + user | user + session |

Agent **SHALL NOT** 将附件目录作为默认可写根。Agent 经 backend 访问平台 Skills **SHALL** 使用 `/skills/extensions/`；用户 Skills **SHALL** 使用 `/skills/custom/`。

#### Scenario: Skills 仍为全局只读

- **WHEN** 超级 Agent `write_file` 至 `/skills/extensions/foo.md`
- **THEN** **SHALL NOT** 修改 `extensions/skills` 或容器 `/skills/`

#### Scenario: 用户 skills 目录 Agent 只读

- **WHEN** Agent `write_file` 至 `/skills/custom/foo/SKILL.md`
- **THEN** **SHALL NOT** 修改 `.data/users/{uid}/skills/` 下文件

#### Scenario: 任务产物写入 research 子目录

- **WHEN** Agent `write_file` 至 `/research/notes.md`
- **THEN** 变更 **SHALL** 落在 `sessions/{sid}/workspace/research/notes.md`，**SHALL NOT** 写入其它 session 或 `skills/`

#### Scenario: filesystem 默认盘写入 workspace 根

- **WHEN** 当前 run 为 session `s1`，Agent 经 filesystem 工具 `write_file("/notes.md")`
- **THEN** SHALL 写入 `sessions/s1/workspace/notes.md`，且 `execute("cat /notes.md")` **SHALL** 读取同一文件
