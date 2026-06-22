## Purpose

本能力规定 Noesis **Agent AIO 沙箱**：每 **`(user_id, session_id)` 一个** AIO 容器；经 **`AioSandboxBackend(BaseSandbox)`** + **`agent_sandbox`** SDK 接入 deepagents；替代 `LocalShellBackend`；API 密钥 **SHALL NOT** 进入沙箱。

## ADDED Requirements

### Requirement: 系统 SHALL 为每个 session 提供独立 AIO 沙箱

需要 filesystem backend 且 `current_user` 与 `session_id` 有效时，系统 SHALL 经 `sandbox-runner` 为 **`(user_id, session_id)`** 创建或复用 **一个** AIO 容器。不同用户 **SHALL** 使用不同容器；同一用户不同 session **SHALL** 使用不同容器。

#### Scenario: 首次使用创建 session 沙箱

- **WHEN** 用户 `u1` 在 session `s1` 首次触发 filesystem 或 `execute`
- **THEN** runner SHALL 创建或绑定 `aio-{hash(u1,s1)}`（或等价命名），容器 **SHALL** 以 `session_id` 为 lifecycle 主键之一

#### Scenario: 同 session 复用沙箱

- **WHEN** 用户 `u1` 在 session `s1` 发起第二次 Agent run，且 `s1` 沙箱仍 alive
- **THEN** SHALL 复用同一 AIO 容器与 `base_url`

#### Scenario: 同用户不同 session 独立沙箱

- **WHEN** 用户 `u1` 在 session `s1` 与 `s2` 并行 Agent
- **THEN** **SHALL** 使用两个 AIO 容器，**SHALL NOT** 共用同一 `base_url`

#### Scenario: sandbox-runner 不可用

- **WHEN** runner 不可达或 AIO 容器未 ready
- **THEN** Agent SHALL 明确失败，**SHALL NOT** 回退 `LocalShellBackend`

### Requirement: Agent virtual 根 SHALL 对应当前 session workspace

`create_session_sandbox_backend(user_id, session_id)` SHALL 使 deepagents virtual **`/`** 映射到 AIO 容器内 **`/workspace`**（bind 当前 session 宿主机 workspace）；Agent **SHALL** 继续使用 `/research/...` 等路径。

#### Scenario: Prompt 路径不变

- **WHEN** Agent `write_file` 写入 `/research/demo/report.md`
- **THEN** 文件 SHALL 落在 `.data/agent_workspace/users/{uid}/sessions/{sid}/workspace/research/demo/report.md`

#### Scenario: Skills 路径不变

- **WHEN** Agent `read_file` 读取 `/skills/baoyu-url-to-markdown/SKILL.md`
- **THEN** SHALL 解析至容器内 ro `/skills/baoyu-url-to-markdown/SKILL.md`

### Requirement: AioSandboxBackend SHALL 实现 BaseSandbox

`AioSandboxBackend` **SHALL** 继承 `deepagents.backends.sandbox.BaseSandbox`，**SHALL** 通过 `agent_sandbox.Sandbox(base_url=...)` 实现：

- `execute()` → `client.shell.exec_command`
- `upload_files()` / `download_files()` → AIO file API

**SHALL NOT** 在 API 进程 `subprocess.run(..., shell=True)` 执行 Agent 命令。

#### Scenario: execute 不经 API 进程 shell

- **WHEN** Agent 调用 `execute`
- **THEN** SHALL 由 `AioSandboxBackend` 经 HTTP 在 AIO 容器内执行

### Requirement: AIO 调用 SHALL 按 session 串行化

因 AIO 容器内 shell 为单持久会话，对同一 `(user_id, session_id)`，`AioSandboxBackend` **SHALL** 对 `execute`、`upload_files`、`download_files` **mutex 串行**。

#### Scenario: 同 session 并行 tool 调用

- **WHEN** 同一 session 内两个 `execute` tool call 几乎同时到达
- **THEN** backend SHALL 串行转发至 AIO，**SHALL NOT** 依赖 AIO 并发 shell 正确性

### Requirement: 沙箱挂载 SHALL 最小化

AIO 容器 volume **SHALL** 仅：

- 当前 session workspace → `/workspace`（rw）
- `extensions/skills` → `/skills`（ro）

**SHALL NOT** mount API `/app`、`.env`、其它 session 目录、整棵 `users/{uid}/`、附件、checkpoint。

#### Scenario: execute 不可读 API 密钥

- **WHEN** Agent `execute(command="cat /app/.env")`
- **THEN** **SHALL NOT** 返回 Noesis 业务密钥（路径不存在或不可读）

#### Scenario: 不可读其它 session workspace

- **WHEN** 当前 run 为 session `s1`，Agent `execute` 尝试读取 `s2` 工作区文件
- **THEN** **SHALL NOT** 成功（`s2` 目录未 mount 进该容器）

#### Scenario: Skills 只读

- **WHEN** Agent `write_file` 至 `/skills/foo.md`
- **THEN** **SHALL NOT** 修改宿主机 `extensions/skills`

### Requirement: 浏览器与 CDP Skills

AIO 镜像 **SHALL** 提供浏览器运行时（内置 browser API / Chromium 等）。`baoyu-url-to-markdown` 等 Skills **SHALL** 经 `execute` 在 session 容器内运行。

系统 **SHALL** 为 session 容器注入：

- `SANDBOX_HEADLESS=1`（或等价）
- `URL_CHROME_PATH`（容器内 Chromium 路径）
- `BAOYU_CHROME_PROFILE_DIR=/workspace/.chrome-profile`（或 session workspace 下路径）

#### Scenario: baoyu skill 在 AIO 容器执行

- **WHEN** 深度研究 Agent 调用 baoyu skill 脚本
- **THEN** SHALL 在对应 session 的 AIO 容器内 `execute`，**SHALL NOT** 在 API 容器执行

#### Scenario: 未来 browser API skill

- **WHEN** 未来 Skill 声明使用 AIO `client.browser.*`
- **THEN** 实现 **MAY** 在 `AioSandboxBackend` 或独立 helper 中调用，**SHALL** 仍限于当前 session 的 `base_url`

### Requirement: 沙箱环境与密钥

AIO 容器 env **SHALL NOT** 含 `MODEL_API_KEY`、`TAVILY_API_KEY`、`JWT_SECRET_KEY` 等 API 密钥；**MAY** 含 `GH_TOKEN`（scoped）。

#### Scenario: 沙箱内无 Tavily Key

- **WHEN** Agent `execute(command="env")`
- **THEN** **SHALL NOT** 含 `TAVILY_API_KEY`

### Requirement: 沙箱 idle 回收 SHALL 尊重 in-flight Agent

`SandboxService` **SHALL** 维护 per-session in-flight 计数。

runner **SHALL NOT** 因 idle TTL 停止 session 沙箱，当该 session in-flight **> 0**。

#### Scenario: 流式进行中不回收

- **WHEN** session `s1` 仍在 SSE 流式 Agent 运行中，且 idle TTL 已到
- **THEN** runner **SHALL NOT** 停止 `s1` 的 AIO 容器

#### Scenario: session idle 后回收

- **WHEN** session `s1` in-flight 为 0 且超过 `sandbox_idle_ttl_seconds`
- **THEN** runner **MAY** 停止 `s1` AIO 容器；磁盘 `sessions/s1/` **SHALL** 保留

### Requirement: 软删 session SHALL 销毁 session 沙箱

软删 session **SHALL** 调用 `destroy_session_sandbox(user_id, session_id)`，**SHALL** 删除该 session 磁盘子树（见 `agent-workspace`：须先 cancel 进行中 run）。

#### Scenario: 删 session 销毁容器

- **WHEN** 用户删除 session `s1`
- **THEN** `s1` 的 AIO 容器 **SHALL** 被 stop/remove；`sessions/s1/` 磁盘 **SHALL** 删除

### Requirement: sandbox-runner API 与安全

runner **SHALL** 提供内网 lifecycle API：

- `PUT /internal/sandboxes/{user_id}/{session_id}` → 确保容器存在，返回 `{ "base_url": "..." }`
- `DELETE /internal/sandboxes/{user_id}/{session_id}`
- `GET /health`

**SHALL** 使用 `SANDBOX_RUNNER_TOKEN`；**SHALL NOT** 对公网暴露。**SHALL** 写 audit log（user_id、session_id、action、duration_ms）。

可选 `sandbox_max_replicas`：全局 concurrent session 沙箱上限；超限 **SHALL** 优先 evict idle 最久 session 容器。

#### Scenario: 未授权拒绝

- **WHEN** 请求无有效 `SANDBOX_RUNNER_TOKEN`
- **THEN** runner SHALL 返回 HTTP 401

### Requirement: Docker 部署 SHALL 使用宿主机 bind 路径

runner 创建 AIO 容器时的 volume bind **SHALL** 使用 `NOESIS_HOST_DATA_DIR`；`agent_workspace` **SHALL** 与 checkpoint 等位于同一可挂载 runtime 根。

#### Scenario: Compose 下 workspace 可写

- **WHEN** 生产 compose 启动且 Agent 写入 workspace
- **THEN** 文件 **SHALL** 持久化到 host/命名卷，且 AIO bind mount **SHALL** 使容器内 `/workspace` 可见同一内容

### Requirement: 依赖 agent-sandbox SDK

backend **SHALL** 声明 PyPI 依赖 `agent-sandbox`；版本 **SHALL** 与 `SANDBOX_AIO_IMAGE` 在文档或 lockfile 中配套说明。

#### Scenario: SDK 与容器不匹配

- **WHEN** SDK 与 AIO 容器 API 版本不兼容导致 HTTP 4xx/5xx
- **THEN** Agent **SHALL** 返回明确 sandbox 错误，**SHALL NOT** 静默回退 LocalShell
