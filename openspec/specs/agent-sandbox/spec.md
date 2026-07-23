# agent-sandbox Specification

## Purpose

本能力规定 Noesis **AIO 沙箱**的隔离与生命周期：每用户一个 AIO 容器、session 级 virtual workspace 根、`sandbox-runner` 内网 API、rw/ro 挂载策略，以及与 `agent-runtime-paths` 路径布局的对齐。删 session **SHALL NOT** 销毁用户沙箱。
## Requirements
### Requirement: 沙箱环境与密钥

沙箱容器 env **SHALL NOT** 含 API 业务密钥；**MAY** 含 scoped `GH_TOKEN`。

#### Scenario: 沙箱内无 Tavily Key

- **WHEN** Agent `execute(command="env")`
- **THEN** **SHALL NOT** 含 `TAVILY_API_KEY`

### Requirement: 沙箱 idle 回收 SHALL 尊重 in-flight Agent

`SandboxService` **SHALL** 维护 in-flight 计数（按 sandbox lifecycle 键：session 或 user）。

runner **SHALL NOT** 因 idle TTL 停止仍有 in-flight > 0 的沙箱。

#### Scenario: 流式进行中不回收

- **WHEN** session 仍在 SSE 流式运行，且 idle TTL 已到
- **THEN** runner **SHALL NOT** 停止该 session 沙箱

#### Scenario: idle 后回收

- **WHEN** 对应 sandbox 的 in-flight 为 0 且超过 TTL
- **THEN** runner **MAY** 停止容器；磁盘 `users/{uid}/sessions/{sid}/workspace` **SHALL** 保留

### Requirement: execute SHALL 保留 Shell 操作符语义

系统对 `execute` 命令做任何路径处理时 **SHALL NOT** 使用会把 `>`、`>>`、`<`、`|`、`||`、`&&`、`;`、`(`、`)` 等变成普通 argv 的 `shlex.split` → `shlex.join` 整命令回写。

目标态：**SHALL NOT** 对 `execute` 做 Skills/虚拟绝对路径的 token rewrite；Agent **SHALL** 使用相对路径或容器真实挂载路径（`/workspace/...`、`/skills/public/...`、`/skills/personal/...`）。

#### Scenario: 重定向写入 workspace

- **WHEN** Agent `execute(command="printf done > /workspace/out.txt")`（或 cwd 为 `/workspace` 时 `printf done > out.txt`）
- **THEN** 文件 SHALL 出现在当前 session 的宿主机 workspace，且内容为 `done`

#### Scenario: 管道与链式命令

- **WHEN** Agent `execute(command="mkdir -p out && printf x > out/a.txt && cat out/a.txt | wc -c")`
- **THEN** 命令 SHALL 按 Shell 语义执行成功，**SHALL NOT** 将 `&&`、`>`、`|` 当作字面参数

### Requirement: 沙箱挂载 SHALL 仅为当前 session workspace 与双 Skills

runner 创建沙箱时 **SHALL** 仅挂载：

- 当前 session workspace（宿主机）→ `/workspace`（rw）
- 公共 skills（宿主机）→ `/skills/public`（ro）
- 个人 skills（宿主机）→ `/skills/personal`（ro）

**SHALL NOT** 将整个 `users/{user_id}/` 树 rw 挂载进容器。

#### Scenario: 不可经 Shell 写个人 Skills

- **WHEN** Agent `execute(command="echo hack > /skills/personal/foo/SKILL.md")`
- **THEN** **SHALL** 失败（只读挂载），宿主机个人 Skills 文件 **SHALL NOT** 被修改

#### Scenario: 不可经 Shell 访问其它 session

- **WHEN** 当前 run 为 session `s1`，Agent `execute` 尝试读取另一 session `s2` 的 workspace 文件
- **THEN** **SHALL NOT** 成功（`s2` 未挂载）

#### Scenario: 记忆与附件不在默认挂载面

- **WHEN** Agent `execute(command="ls /workspace")` 或探测用户根
- **THEN** **SHALL NOT** 默认列出 `AGENTS.md`、`USER.md`、其它 session、`uploads/`、`attachments/` 作为可写树

### Requirement: backend handle 缓存 SHALL 在容器缺失时失效并重建

`SandboxService` 的 handle 缓存 **SHALL** 仅作优化。当 runner 返回容器不存在（如 HTTP 404）或等价错误时，backend **SHALL** 清除该用户/session 的缓存条目，**SHALL** 重新 `ensure` 并重试执行 **至少一次**。

#### Scenario: idle 回收后自动恢复

- **WHEN** runner 已回收容器且 backend 仍持有旧 handle，随后 Agent 发起 `execute`
- **THEN** backend SHALL 失效缓存、重建沙箱并成功执行（或返回明确不可恢复错误，**SHALL NOT** 对该用户后续请求永久 404）

### Requirement: 沙箱进程 SHALL 非 root 运行

`sandbox-slim`（或等价生产镜像）**SHALL** 声明非 root `USER`。容器内有效 UID **SHALL NOT** 为 0。

#### Scenario: 探针非 root

- **WHEN** 在运行中的沙箱执行 `id -u`
- **THEN** 输出 **SHALL NOT** 为 `0`

### Requirement: 生产 backend SHALL 仅为 docker 或 local_shell

配置 `sandbox.backend` **SHALL** 仅允许 `docker` 与 `local_shell`。**SHALL NOT** 提供 `aio` 选项。

#### Scenario: 配置 aio 被拒绝

- **WHEN** 配置或环境指定 `sandbox.backend=aio`
- **THEN** 系统 SHALL 在启动或创建 backend 时失败并给出明确错误，**SHALL NOT** 静默回退

### Requirement: 系统 SHALL 为每个会话提供隔离沙箱执行环境

需要 filesystem backend 且 `current_user` 有效时，系统 SHALL 经 `sandbox-runner` 为当前 **`(user_id, session_id)`** 创建或复用沙箱执行环境（默认 **per-session 容器**；若实现为 per-user 容器则 **SHALL** 仅挂载该 session 的挂载表）。不同用户 **SHALL** 使用不同容器。同用户不同 session **SHALL NOT** 共享可写 workspace 挂载。

#### Scenario: 首次使用创建会话沙箱

- **WHEN** 用户 `u1` 在 session `s1` 首次触发 filesystem 或 `execute`
- **THEN** runner SHALL 创建或绑定对应该 session 的沙箱；lifecycle 主键 **SHALL** 包含 `session_id`（或等价确保挂载仅服务 `s1`）

#### Scenario: 同用户换 session 不共享可写盘

- **WHEN** 用户 `u1` 在 session `s2` 发起 Agent
- **THEN** `/workspace` **SHALL** 映射 `s2` 的 workspace，**SHALL NOT** 映射 `s1` 的 workspace

#### Scenario: 不同用户隔离

- **WHEN** 用户 `u1` 与 `u2` 同时运行 Agent
- **THEN** **SHALL** 使用两个沙箱容器，**SHALL NOT** 共用执行端点

#### Scenario: sandbox-runner 不可用

- **WHEN** runner 不可达或沙箱未 ready
- **THEN** Agent SHALL 明确失败，**SHALL NOT** 在生产 `docker` 模式下回退 `LocalShellBackend`

### Requirement: Agent workspace 根 SHALL 映射容器 /workspace

`create_agent_backend(user_id, session_id)` SHALL 使 Agent 默认可写根对应容器内 **`/workspace`**（宿主机当前 session workspace）。深度调研等场景的 `research/` **SHALL** 为 `/workspace/research/` 子目录。

公共 Skills **SHALL** 经 `/skills/public/` 只读访问；个人 Skills **SHALL** 经 `/skills/personal/` 只读访问。

用户记忆 **SHALL** 继续经 MemoryMiddleware / 面板 API，**SHALL NOT** 要求映射进沙箱 Shell 可见路径。

任务可写产物 **SHALL** 默认落在 `/workspace` 根或任务子目录。

#### Scenario: workspace 根写入

- **WHEN** Agent `write_file` 写入 `/notes.md` 或相对路径 `notes.md`
- **THEN** 文件 SHALL 落在 `.data/users/{uid}/sessions/{sid}/workspace/notes.md`

#### Scenario: 公共 Skills 路径

- **WHEN** Agent `read_file` 读取 `/skills/public/deep-research-v2/SKILL.md`
- **THEN** SHALL 解析至容器内 ro `/skills/public/deep-research-v2/SKILL.md`

#### Scenario: 个人 Skills 路径

- **WHEN** Agent `read_file` 读取 `/skills/personal/my-tool/SKILL.md`
- **THEN** SHALL 解析至容器内 ro `/skills/personal/my-tool/SKILL.md`

### Requirement: DockerExecSandboxBackend SHALL 实现 BaseSandbox

生产路径 **SHALL** 经 runner 的 Docker Exec（或等价）实现 `execute`、文件上下传。**SHALL NOT** 在 API 进程本地 shell 执行生产 Agent 命令（`local_shell` 仅开发/测试）。

#### Scenario: execute 不经 API 进程 shell

- **WHEN** `sandbox.backend=docker` 且 Agent 调用 `execute`
- **THEN** SHALL 在用户沙箱容器内经 runner 执行

### Requirement: 沙箱调用 SHALL 按 session 串行化

对同一 `(user_id, session_id)`，沙箱 backend **SHALL** mutex 串行 `execute` 与 upload/download。

对同一 `user_id` 不同 `session_id`，**MAY** 并行。

#### Scenario: 同 session 并行 tool 调用

- **WHEN** session `s1` 内两个 `execute` 几乎同时到达
- **THEN** backend SHALL 串行转发至该 session 沙箱

### Requirement: 软删 session SHALL 可销毁该 session 沙箱

软删 session **SHALL** cancel run 并 `delete_session_data`；**SHALL** 销毁该 session 对应沙箱容器（若存在）。**SHALL NOT** 删除用户记忆或个人 Skills。

#### Scenario: 删 session 销毁其沙箱

- **WHEN** 用户删除 session `s1` 且仍有 session `s2`
- **THEN** `sessions/s1/` **SHALL** 删除；`s1` 沙箱容器 **SHALL** 被销毁；`s2` 沙箱 **MAY** 继续

### Requirement: sandbox-runner SHALL 提供 lifecycle API

runner **SHALL** 提供内网 API 以确保/销毁沙箱（路径可按 session 键演进，如 `PUT/DELETE /internal/sandboxes/{user_id}/{session_id}` 或文档等价），以及 `GET /health`。

**SHALL** 使用 `SANDBOX_RUNNER_TOKEN`；**SHALL NOT** 公网暴露。

#### Scenario: 未授权拒绝

- **WHEN** 请求无有效 token
- **THEN** HTTP 401

#### Scenario: 确保沙箱

- **WHEN** backend 确保 `(u1, s1)` 沙箱且 token 有效
- **THEN** runner **SHALL** 返回可达的执行句柄（如 `container_id` / `base_url` 等价字段）

### Requirement: Docker 部署 SHALL 使用宿主机真实路径做 bind

runner 向 Docker daemon 声明的 bind **SHALL** 使用宿主机绝对路径（`NOESIS_HOST_DATA_DIR` 等），**SHALL NOT** 使用仅在 runner 容器命名空间有效、宿主机不存在的路径。

#### Scenario: Compose workspace 持久化同源

- **WHEN** Agent 写入 `/workspace/notes.md`
- **THEN** backend 与前端读取的 session workspace **SHALL** 与沙箱写入为同一宿主机文件

### Requirement: execute 工作目录 SHALL 为 /workspace

Docker Exec backend 的 `execute` **SHALL** 将工作目录设为 `/workspace`。

Agent Prompt **SHALL** 说明：shell cwd 为 workspace 根；相对路径即产物路径。

#### Scenario: pwd 为 /workspace

- **WHEN** Agent `execute(command="pwd")`
- **THEN** 输出 **SHALL** 为 `/workspace`

#### Scenario: 相对路径写入

- **WHEN** Agent `execute(command="printf ok > notes.md")`
- **THEN** 宿主机当前 session workspace 下 **SHALL** 存在 `notes.md`，内容为 `ok`

