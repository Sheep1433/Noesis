## ADDED Requirements

### Requirement: execute SHALL 与 filesystem 工具解析同一虚拟路径

因 deepagents `CompositeBackend.execute()` **仅委托 default backend**，系统 **SHALL** 在 **workspace** `PrefixBackend.execute()`（`default` 链）内、调用 inner `execute` 之前，将命令 token 中的 Agent 虚拟绝对路径 rewrite 为与 `read_file` / `write_file` 相同的物理目标。extensions / custom / memory 的 `PrefixBackend` **SHALL NOT** 作为 execute rewrite 落点（其 `execute()` 不会被调用）。

映射 **SHALL** 使用 `PathRewriteContext`（与 `build_agent_filesystem_backend` 同源构造），**SHALL** 经 token 级处理（**SHALL NOT** 对整条 command 裸 `str.replace`）。

**Tier 1**（显式前缀，最长优先）：

| 虚拟前缀 | AIO 容器物理目标 |
|----------|------------------|
| `/research/` | `/workspace/sessions/{session_id}/workspace/research/` |
| `/skills/extensions/` | `/skills/` |
| `/skills/custom/` | `/workspace/skills/` |
| `/memory/AGENTS.md` | `/workspace/AGENTS.md` |
| `/memory/USER.md` | `/workspace/USER.md` |

**Tier 2**（workspace 根）：未命中 Tier 1 且非系统 denylist 的虚拟绝对路径 token（如 `/notes.md`、`/summary_offload/...`）**SHALL** rewrite 为 `/workspace/sessions/{session_id}/workspace{token}`。

`local_shell` 与 `aio` **SHALL** 共用同一 rewrite 逻辑；目标为等价 host 绝对路径。

#### Scenario: execute 读取 research 文件与 read_file 一致

- **WHEN** 当前 session 为 `s1`，Agent 已 `write_file` 写入 `/research/demo/report.md`，随后 `execute(command="cat /research/demo/report.md")`
- **THEN** 命令 **SHALL** 在容器内读取 `/workspace/sessions/s1/workspace/research/demo/report.md` 并返回文件内容

#### Scenario: execute 读取 workspace 根文件

- **WHEN** Agent `write_file("/notes.md", "x")` 后 `execute(command="cat /notes.md")`
- **THEN** **SHALL** 读取 `/workspace/sessions/{sid}/workspace/notes.md` 并返回 `x`

#### Scenario: execute 运行 extensions skill 脚本

- **WHEN** Agent `execute(command="python3 /skills/extensions/deep-research-v2/run.py")`
- **THEN** 命令 **SHALL** rewrite 为容器内 `/skills/deep-research-v2/run.py` 并执行

#### Scenario: execute 读取记忆文件与 super-agent 物理路径一致

- **WHEN** 宿主机存在 `users/{uid}/AGENTS.md` 且 AIO mount 就绪，Agent `execute(command="cat /memory/AGENTS.md")`
- **THEN** 命令 **SHALL** rewrite 为 `cat /workspace/AGENTS.md` 并返回该文件内容（与 `read_file("/memory/AGENTS.md")` 读同一文件）

#### Scenario: local_shell 模式行为一致

- **WHEN** `sandbox.backend=local_shell`，Agent `execute(command="cat /research/notes.md")` 且该文件已由 `write_file` 写入
- **THEN** **SHALL** 读取 `{REPO_ROOT}/.data/users/{uid}/sessions/{sid}/workspace/research/notes.md`

#### Scenario: 引号内路径不被误 rewrite

- **WHEN** Agent `execute(command='echo "see /research/foo"')`
- **THEN** 输出 **SHALL NOT** 将引号内字符串改写为物理 workspace 路径

### Requirement: execute 工作目录 SHALL 为 session workspace 根

`AioSandboxBackend.execute` **SHALL** 将 `exec_dir` 设为 `/workspace/sessions/{session_id}/workspace`。

Agent Prompt **SHALL** 说明：shell cwd 为 workspace 根；`/research/foo` 等价于 `research/foo`；依赖 `cd` 的后续命令 **SHALL** 在同一 command 用 `&&` 链接。

**已知限制（首版）**：`pwd` 输出 **MAY** 仍为物理路径 `/workspace/sessions/{sid}/workspace`；`execute` 直接访问 `/workspace/AGENTS.md` **MAY** 成功（mount bypass），本能力 **SHALL NOT** 将其作为规范路径。

#### Scenario: pwd 输出物理路径

- **WHEN** Agent `execute(command="pwd")` 且 session 为 `s1`
- **THEN** 输出 **SHALL** 为 `/workspace/sessions/s1/workspace`

#### Scenario: 链式 cd 与 make

- **WHEN** Agent `execute(command="cd /research/demo && make")`
- **THEN** `cd` 与 `make` **SHALL** 在同一 shell 调用内执行，且 `cd` 目标 **SHALL** 经 rewrite 指向 `.../workspace/research/demo`

## MODIFIED Requirements

### Requirement: Agent virtual 根 SHALL 对应当前 session workspace

`create_agent_backend(user_id, session_id)` SHALL 使 deepagents virtual **`/`** 映射到容器内 **`/workspace/sessions/{session_id}/workspace`**；Agent **SHALL** 继续使用 `/research/...`、`/memory/...` 等路径而 **SHALL NOT** 在 Prompt 中嵌入 `sessions/{session_id}/`。

任务可写产物 **SHALL** 默认落在 virtual `/research/` 子目录下；workspace 根虚拟路径（`/notes.md` 等）**SHALL** 仍受支持。

#### Scenario: Prompt 路径不变

- **WHEN** Agent `write_file` 写入 `/research/demo/report.md`（当前 session `s1`）
- **THEN** 文件 SHALL 落在 `.data/users/{uid}/sessions/s1/workspace/research/demo/report.md`

#### Scenario: extensions Skills 路径

- **WHEN** Agent `read_file` 读取 `/skills/extensions/deep-research-v2/SKILL.md`
- **THEN** SHALL 解析至容器内 ro `/skills/deep-research-v2/SKILL.md`

#### Scenario: custom Skills 路径

- **WHEN** Agent `read_file` 读取 `/skills/custom/my-tool/SKILL.md`
- **THEN** SHALL 解析至容器内 `/workspace/skills/my-tool/SKILL.md`

#### Scenario: 单层 /skills/{name} 非权威路径

- **WHEN** Agent `read_file` 读取 `/skills/deep-research-v2/SKILL.md`（未带 `extensions/` 或 `custom/`）
- **THEN** 系统 **SHALL NOT** 保证解析至平台或用户 Skills 目录；**SHALL** 按 default workspace 路由（通常 `file_not_found`）
