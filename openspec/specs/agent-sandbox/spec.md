# agent-sandbox Specification

## Purpose

本能力规定 Noesis **AIO 沙箱**的隔离与生命周期：每用户一个 AIO 容器、session 级 virtual workspace 根、`sandbox-runner` 内网 API、rw/ro 挂载策略，以及与 `agent-runtime-paths` 路径布局的对齐。删 session **SHALL NOT** 销毁用户沙箱。
## Requirements
### Requirement: 系统 SHALL 为每个用户提供独立 AIO 沙箱

需要 filesystem backend 且 `current_user` 有效时，系统 SHALL 经 `sandbox-runner` 为 **`user_id`** 创建或复用 **一个** AIO 容器。**SHALL NOT** 为每个 `session_id` 单独创建容器。不同用户 **SHALL** 使用不同容器。

#### Scenario: 首次使用创建用户沙箱

- **WHEN** 用户 `u1` 在 session `s1` 首次触发 filesystem 或 `execute`
- **THEN** runner SHALL 创建或绑定 `aio-{hash(user_id)}`；容器 lifecycle 主键 **SHALL** 为 `user_id`

#### Scenario: 同用户换 session 复用沙箱

- **WHEN** 用户 `u1` 在 session `s2` 发起 Agent，且 `u1` 沙箱已存在
- **THEN** SHALL 复用同一 AIO 容器与 `base_url`；backend SHALL 切换为 session `s2` 的 virtual 根

#### Scenario: 不同用户隔离

- **WHEN** 用户 `u1` 与 `u2` 同时运行 Agent
- **THEN** **SHALL** 使用两个 AIO 容器，**SHALL NOT** 共用 `base_url`

#### Scenario: sandbox-runner 不可用

- **WHEN** runner 不可达或 AIO 容器未 ready
- **THEN** Agent SHALL 明确失败，**SHALL NOT** 回退 `LocalShellBackend`

### Requirement: Agent virtual 根 SHALL 对应当前 session workspace

`create_user_sandbox_backend(user_id, session_id)` SHALL 使 deepagents virtual **`/`** 映射到容器内 **`/workspace/sessions/{session_id}/workspace`**；Agent **SHALL** 继续使用 `/research/...` 等路径而 **SHALL NOT** 在 Prompt 路径中嵌入 `sessions/{session_id}/`。

#### Scenario: Prompt 路径不变

- **WHEN** Agent `write_file` 写入 `/research/demo/report.md`（当前 session `s1`）
- **THEN** 文件 SHALL 落在 `.data/users/{uid}/sessions/s1/workspace/research/demo/report.md`

#### Scenario: Skills 路径不变

- **WHEN** Agent `read_file` 读取 `/skills/baoyu-url-to-markdown/SKILL.md`
- **THEN** SHALL 解析至容器内 ro `/skills/baoyu-url-to-markdown/SKILL.md`

### Requirement: AioSandboxBackend SHALL 实现 BaseSandbox

**SHALL** 经 `agent_sandbox.Sandbox(base_url=...)` 实现 `execute`、`upload_files`、`download_files`。**SHALL NOT** 在 API 进程本地 shell 执行 Agent 命令。

#### Scenario: execute 不经 API 进程 shell

- **WHEN** Agent 调用 `execute`
- **THEN** SHALL 在用户 AIO 容器内经 HTTP 执行

### Requirement: AIO 调用 SHALL 按 session 串行化

对同一 `(user_id, session_id)`，`AioSandboxBackend` **SHALL** mutex 串行 `execute` 与 upload/download（AIO 单 shell 会话）。

对同一 `user_id` 不同 `session_id`，**MAY** 并行到达 backend，但 **SHALL** 各自 mutex，**SHALL NOT** 交叉污染 AIO shell 状态。

#### Scenario: 同 session 并行 tool 调用

- **WHEN** session `s1` 内两个 `execute` 几乎同时到达
- **THEN** backend SHALL 串行转发至 AIO

### Requirement: 沙箱挂载 SHALL 为 users/{user_id} 树

AIO 容器 **SHALL** rw mount：

- `{NOESIS_HOST_DATA_DIR}/users/{user_id}/` → `/workspace`

**SHALL** ro mount：

- `extensions/skills` → `/skills`

**SHALL NOT** mount API `/app`、`.env`、其它用户目录、附件、checkpoint。

#### Scenario: execute 不可读 API 密钥

- **WHEN** Agent `execute(command="cat /app/.env")`
- **THEN** **SHALL NOT** 返回 Noesis 业务密钥

#### Scenario: 同用户 execute 可读其它 session 工作区

- **WHEN** 当前 run 为 session `s1`，Agent `execute` 读取 `/workspace/sessions/s2/workspace/notes.md`（同用户 `s2` 存在）
- **THEN** **MAY** 成功返回内容（供跨 session 汇总等场景）

#### Scenario: 不同用户不可互读

- **WHEN** 用户 `u1` Agent 尝试访问 `u2` 的 workspace
- **THEN** **SHALL NOT** 成功（`u2` 目录未 mount 进 `u1` 容器）

#### Scenario: filesystem 默认盘不误写其它 session

- **WHEN** 当前 run 为 session `s1`，Agent 经 filesystem 工具 `write_file("/notes.md")`
- **THEN** SHALL 写入 `sessions/s1/workspace/notes.md`，**SHALL NOT** 默认写入 `sessions/s2/`

#### Scenario: Skills 只读

- **WHEN** Agent `write_file` 至 `/skills/foo.md`
- **THEN** **SHALL NOT** 修改宿主机 `extensions/skills`

### Requirement: 浏览器与 CDP Skills

**SHALL** 在用户 AIO 容器内 `execute` 运行 baoyu 等 Skills。

**SHALL** 按 session 注入：

- `SANDBOX_HEADLESS=1`
- `URL_CHROME_PATH`
- `BAOYU_CHROME_PROFILE_DIR=/workspace/sessions/{session_id}/workspace/.chrome-profile`
- CDP 端口 env（如 `SANDBOX_CDP_PORT`）按 session 区分

#### Scenario: 同用户两 session CDP

- **WHEN** 用户 `u1` 在 `s1` 与 `s2` 先后触发 CDP 抓页
- **THEN** **SHALL** 共用 **一个** AIO 容器；profile/端口 **SHALL** 按 session 区分，**SHALL NOT** 因固定端口必然互斥失败

### Requirement: 沙箱环境与密钥

AIO 容器 env **SHALL NOT** 含 API 业务密钥；**MAY** 含 scoped `GH_TOKEN`。

#### Scenario: 沙箱内无 Tavily Key

- **WHEN** Agent `execute(command="env")`
- **THEN** **SHALL NOT** 含 `TAVILY_API_KEY`

### Requirement: 沙箱 idle 回收 SHALL 尊重 in-flight Agent

`SandboxService` **SHALL** 维护 **per-user** in-flight 计数（该 user 任一 session run 期间 >0）。

runner **SHALL NOT** 因 idle TTL 停止用户沙箱，当该 user in-flight **> 0**。

#### Scenario: 流式进行中不回收

- **WHEN** 用户 `u1` 某 session 仍在 SSE 流式运行，且 idle TTL 已到
- **THEN** runner **SHALL NOT** 停止 `u1` 沙箱

#### Scenario: 全 idle 后回收

- **WHEN** 用户 `u1` 全部 session in-flight 为 0 且超过 TTL
- **THEN** runner **MAY** 停止 `u1` AIO 容器；磁盘 `users/u1/` **SHALL** 保留

### Requirement: 软删 session SHALL NOT 销毁用户沙箱

软删 session **SHALL** cancel run 并 `delete_session_data`（见 `agent-runtime-paths`）；**SHALL NOT** 调用 `destroy_user_sandbox`（除非该 user 无其它 session 且产品另行规定——默认 **不** destroy）。

#### Scenario: 删 session 保留用户沙箱

- **WHEN** 用户删除 session `s1` 且仍有 session `s2`
- **THEN** `sessions/s1/` **SHALL** 删除；`u1` AIO 容器 **MAY** 继续服务 `s2`

### Requirement: sandbox-runner SHALL 提供 user 级 lifecycle API

runner **SHALL** 提供内网 API：

- `PUT /internal/sandboxes/{user_id}` → `{ "base_url": "..." }`
- `DELETE /internal/sandboxes/{user_id}`
- `GET /health`

**SHALL** 使用 `SANDBOX_RUNNER_TOKEN`；**SHALL NOT** 公网暴露。

可选 `sandbox_max_replicas`：全局 concurrent **用户**沙箱上限。

#### Scenario: 未授权拒绝

- **WHEN** 请求无有效 token
- **THEN** HTTP 401

#### Scenario: 确保用户沙箱

- **WHEN** backend `PUT /internal/sandboxes/u1` 且 token 有效
- **THEN** runner **SHALL** 返回可达的 `base_url`

### Requirement: Docker 部署 SHALL 使用 NOESIS_HOST_DATA_DIR

runner bind **SHALL** 使用 `NOESIS_HOST_DATA_DIR` 解析 `users/{uid}/` host 路径。

#### Scenario: Compose workspace 持久化

- **WHEN** Agent 写入 workspace
- **THEN** AIO bind 与 backend 写入 **SHALL** 指向同一 host 存储

### Requirement: backend SHALL 依赖 agent-sandbox SDK

backend **SHALL** 声明 PyPI 依赖 `agent-sandbox`；版本 **SHALL** 与 `SANDBOX_AIO_IMAGE` 在文档或 lockfile 中配套说明。

#### Scenario: SDK 与容器 API 不兼容

- **WHEN** SDK 与 AIO 容器 API 版本不匹配导致 HTTP 错误
- **THEN** Agent **SHALL** 返回明确 sandbox 失败，**SHALL NOT** 回退 LocalShell

