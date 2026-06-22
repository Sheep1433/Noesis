## Purpose

本能力规定 Noesis **Agent 用户沙箱**：每 **`user_id` 一个** Docker 容器；磁盘工作区仍 **per-session**；filesystem 与 **`execute` 均 SHALL 无法访问同用户其它 session**；替代 `LocalShellBackend`；API 密钥 **SHALL NOT** 进入沙箱。

## ADDED Requirements

### Requirement: 系统 SHALL 为每个用户提供独立 Docker 沙箱

需要 filesystem backend 且 `current_user` 有效时，系统 SHALL 经 `sandbox-runner` 为 **`user_id`** 创建或复用 **一个** 容器；**SHALL NOT** 为每个 `session_id` 单独创建容器。不同用户 **SHALL** 使用不同容器。

#### Scenario: 首次使用创建用户沙箱

- **WHEN** 用户 `u1` 在 session `s1` 首次触发 filesystem 或 `execute`
- **THEN** runner SHALL 创建或绑定 `sandbox-{hash(user_id)}`，容器名 **SHALL NOT** 以 `session_id` 为主键

#### Scenario: 同用户换会话复用沙箱

- **WHEN** 用户 `u1` 在 session `s2` 发起 Agent，且 `u1` 沙箱已存在
- **THEN** SHALL 复用同一容器；backend SHALL 切换为 session `s2` 的 virtual 根

#### Scenario: sandbox-runner 不可用

- **WHEN** runner 不可达
- **THEN** Agent SHALL 明确失败，**SHALL NOT** 回退 `LocalShellBackend`

### Requirement: Agent virtual 根 SHALL 对应当前 session workspace

`create_user_sandbox_backend(user_id, session_id)` SHALL 使 deepagents virtual **`/`** 映射到当前 session 的 workspace 目录；Agent **SHALL** 继续使用 `/research/...` 等路径而 **SHALL NOT** 在路径中嵌入 `sessions/{session_id}/` 前缀。

容器内物理路径 **SHALL** 为 `/workspace/sessions/{session_id}/workspace/` 下对应文件。

#### Scenario: Prompt 路径不变

- **WHEN** Agent `write_file` 写入 `/research/demo/report.md`
- **THEN** 文件 SHALL 落在 `.data/agent_workspace/users/{uid}/sessions/{sid}/workspace/research/demo/report.md`

### Requirement: DockerSandboxBackend SHALL 实现 BaseSandbox

`execute()` SHALL 经 runner 在用户沙箱内完成；**SHALL NOT** 在 API 进程 `subprocess.run(..., shell=True)`。

#### Scenario: execute 不经 API 进程 shell

- **WHEN** Agent 调用 `execute`
- **THEN** SHALL 由 runner 在沙箱内执行，API 进程 **SHALL NOT** 启动本地 shell

### Requirement: filesystem 工具 SHALL 阻止跨 session 访问

对 default 盘，`read_file`/`write_file`/`edit_file`/`grep`/`glob`/`ls` **SHALL** 拒绝访问当前 session workspace 之外的路径（含指向 `/workspace/sessions/{other_session_id}/` 的路径与 `..` 逃逸）。

#### Scenario: read_file 不可读其它 session

- **WHEN** 当前 run 为 session `s1`，Agent `read_file` 请求同用户 session `s2` 工作区内文件（任意 virtual 或解析后物理路径）
- **THEN** SHALL 返回权限或路径错误，**SHALL NOT** 返回 `s2` 内容

### Requirement: execute SHALL 在 session 级隔离 namespace 内运行

因用户沙箱容器 **MAY** rw 挂载 `.data/agent_workspace/users/{user_id}/` 整棵树，每次 `execute` **SHALL NOT** 在容器全局 mount namespace 内裸跑 shell。

runner **SHALL** 使用 **bubblewrap**（或等价的 per-exec mount namespace）执行命令，且 **SHALL** 仅 bind：

- 当前 session workspace（rw）
- `/skills`（ro）
- 运行 `curl`/`gh`/`bun`/Chromium 所需的系统路径（ro）

#### Scenario: execute 不可 cat 其它 session 文件

- **WHEN** 当前 run 为 session `s1`，Agent `execute(command="cat /workspace/sessions/s2/workspace/secret.txt")`
- **THEN** SHALL 失败或不可见文件内容，**SHALL NOT** 返回 `s2` 工作区内容

#### Scenario: execute 不可读 API 密钥

- **WHEN** Agent `execute(command="cat /app/.env")`
- **THEN** **SHALL NOT** 返回 API 业务密钥

### Requirement: runner exec SHALL 按 session 串行化

对同一 `(user_id, session_id)`，runner **SHALL** 对 `execute` 请求 **mutex 串行**执行，避免并行 shell 争用 cwd 或环境变量。

对同一 `user_id` 不同 `session_id`，**MAY** 并行 exec，但 **SHALL** 各自使用独立 bwrap session 根。

#### Scenario: 同 session 并行 tool 调用

- **WHEN** 同一 session 内两个 `execute` tool call 几乎同时到达
- **THEN** runner SHALL 串行执行，**SHALL NOT** 交叉 cwd

### Requirement: CDP Skill SHALL 避免同容器端口冲突

沙箱内运行 `baoyu-url-to-markdown` 等 CDP Skill 时，系统 **SHALL** 为每个 `session_id` 分配可区分端口或工作目录（如 env `SANDBOX_CDP_PORT`）；端口冲突 **SHALL** 重试备用端口。

#### Scenario: 同用户两 session 先后 CDP 抓页

- **WHEN** 用户 `u1` 在 session `s1` 与 `s2` 分别触发 CDP 抓页
- **THEN** 两次抓页 **SHALL NOT** 因固定 CDP 端口互斥而必然失败（允许重试后成功）

### Requirement: 沙箱挂载与 Skills

用户容器 **SHALL** rw 挂载 `users/{user_id}/` → `/workspace`；**SHALL** ro 挂载 `extensions/skills` → `/skills`。**SHALL NOT** 挂载 API `/app`、`.env`、其它用户目录、附件与 checkpoint。

#### Scenario: Skills 只读

- **WHEN** Agent `write_file` 至 `/skills/foo.md`
- **THEN** **SHALL NOT** 修改宿主机 `extensions/skills`

### Requirement: 沙箱镜像与环境

沙箱镜像 **SHALL** 含 `python3`、`curl`、`gh`、`bun`、Chromium、bubblewrap；API 镜像 **SHALL NOT** 为 Agent 安装 Chromium。

沙箱 env **SHALL NOT** 含 `MODEL_API_KEY`、`TAVILY_API_KEY` 等；egress **SHALL** 允许 HTTPS 公网并拒绝私网/metadata。

#### Scenario: 沙箱内无 Tavily Key

- **WHEN** Agent `execute(command="env")`
- **THEN** **SHALL NOT** 含 `TAVILY_API_KEY`

### Requirement: 沙箱 idle 回收 SHALL 尊重 in-flight Agent

`SandboxService` **SHALL** 维护 per-user in-flight 计数（`run_agent` 开始 +1，结束 -1）。

runner **SHALL NOT** 因 idle TTL 停止用户沙箱，当该 user in-flight 计数 **> 0**。

#### Scenario: 流式进行中不回收

- **WHEN** 用户 `u1` 某 session 仍在 SSE 流式 Agent 运行中，且 idle TTL 已到
- **THEN** runner **SHALL NOT** 停止 `u1` 沙箱

#### Scenario: 全 idle 后回收

- **WHEN** 用户 `u1` in-flight 为 0 且超过 `sandbox_idle_ttl_seconds`
- **THEN** runner **MAY** 停止 `u1` 沙箱；磁盘 `users/u1/` **SHALL** 保留

### Requirement: 软删 session SHALL NOT 销毁用户沙箱

软删 session **SHALL NOT** 调用 `destroy_user_sandbox`；**SHALL** 仅删除该 session 磁盘子树（见 `agent-workspace`：须先 cancel 进行中 run）。

#### Scenario: 删 session 保留用户沙箱

- **WHEN** 用户删除 session `s1` 且仍有 session `s2`
- **THEN** `sessions/s1/` 磁盘子树 **SHALL** 不存在；`u1` 沙箱 **MAY** 继续服务 `s2`

### Requirement: sandbox-runner API 与安全

runner **SHALL** 提供内网 API：

- `PUT /internal/sandboxes/{user_id}`
- `POST /internal/sandboxes/{user_id}/exec`（body：`command`、`timeout`、**`session_id`**）
- `DELETE /internal/sandboxes/{user_id}`（TTL/运维）

**SHALL** 使用 `SANDBOX_RUNNER_TOKEN`；**SHALL NOT** 对公网暴露。exec 命令 **SHALL** 限制最大长度；**SHALL** 写 audit log（`user_id`、`session_id`、command 摘要、exit_code、duration_ms）。

可选 `sandbox_max_replicas`：全局 concurrent 用户沙箱上限；超限时 **SHALL** 优先 evict idle 最久容器。

#### Scenario: 未授权拒绝

- **WHEN** 请求无有效 `SANDBOX_RUNNER_TOKEN`
- **THEN** runner SHALL 返回 HTTP 401

### Requirement: Docker 部署 SHALL 使用宿主机 bind 路径

sandbox-runner 创建容器时的 volume bind **SHALL** 使用 `NOESIS_HOST_DATA_DIR`（宿主机 `.data` 或 compose 卷在 host 的真实路径），**SHALL NOT** 假定 backend 容器内路径等于 Docker daemon 所见路径。

backend 与 runner **SHALL** 共享同一 host 级 runtime 数据布局；`agent_workspace` **SHALL** 与 checkpoint 等 runtime 数据位于同一可挂载根下。

#### Scenario: Compose 下 workspace 可写

- **WHEN** 生产 compose 启动且用户 Agent 写入 workspace
- **THEN** 文件 **SHALL** 持久化到 host/命名卷，且 sandbox bind mount **SHALL** 使 exec 可见同一内容
