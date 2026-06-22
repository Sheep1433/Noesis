## Context

- **产品决策**：采用 **AIO Sandbox** 作为 Agent 执行面；**每 session 一容器**；磁盘工作区仍 per-session。
- **架构约束**：Noesis 继续使用 deepagents `BackendProtocol` + `FilesystemMiddleware` + `CompositeBackend`；**不**引入 DeerFlow `Sandbox` ABC 或自研 bash/read_file tools。
- **AIO 约束**：容器内 shell 为 **单持久会话**；并发 `exec_command` 须 mutex（参考 DeerFlow #1433）。

## Goals / Non-Goals

**Goals:**

- 用户间、session 间：独立 AIO 容器，不可互读 workspace。
- Agent virtual **`/`** = 当前 session workspace；**`/skills/`** = ro Skills（Prompt 不变）。
- 经 `AioSandboxBackend` 实现 `execute` / `upload_files` / `download_files`；其余文件操作默认继承 `BaseSandbox`（经 `execute`）。
- 保留 execute / 浏览器 / Skills / API 侧 web 工具；未来 Skills **MAY** 使用 AIO browser HTTP API。
- `sandbox-runner` 持 Docker socket；API 仅 HTTP 调 runner + AIO 容器。

**Non-Goals:**

- per-user 单容器 + bubblewrap（已废弃）。
- 自建 slim 沙箱镜像 + docker exec runner（已废弃）。
- 移植 DeerFlow 全栈 `AioSandboxProvider` / `/mnt/user-data/...` 路径体系。
- DeerFlow 式 user memory；HITL 审批 UI。

## Decisions

### D1：架构

```
backend (API)
  → sandbox-runner (Docker socket, 内网)     # lifecycle only
       → aio-{hash(user_id, session_id)}     # ghcr.io/agent-infra/sandbox
            :8080 HTTP (agent_sandbox SDK)
  → AioSandboxBackend(BaseSandbox)           # in API process, talks to :8080
  → CompositeBackend(default + /skills/ route)
  → FilesystemMiddleware (unchanged)
```

### D2：Agent virtual `/` vs AIO 容器物理路径

| 视角 | 路径 |
|------|------|
| Agent `read_file("/research/plan.md")` | virtual `/` = session workspace |
| AIO 容器内 workspace mount | `/workspace/research/plan.md` |
| Skills mount | `/skills/...`（ro） |
| 宿主机 | `{DATA_DIR}/agent_workspace/users/{uid}/sessions/{sid}/workspace/...` |

`AioSandboxBackend` **SHALL** 使用 deepagents `virtual_mode=True`，`root_dir` = 容器内 `/workspace`（对应当前 session 宿主机目录）。**SHALL NOT** 要求 LLM 使用 `/mnt/user-data/...` 或 DeerFlow 路径。

### D3：Session 隔离策略

**SHALL** 为每个 `(user_id, session_id)` 创建 **独立** AIO 容器。

runner mount **SHALL** 仅：

- `{host}/agent_workspace/users/{uid}/sessions/{sid}/workspace` → `/workspace`（rw）
- `{repo}/extensions/skills` → `/skills`（ro）

**SHALL NOT** mount 整棵 `users/{uid}/` 或其它 session 目录、API `/app`、`.env`、checkpoint、附件。

filesystem 跨 session 守卫 **MAY** 作为 defense-in-depth（解析 `..` 等），但主隔离边界为 **容器 + mount 范围**。

### D4：AioSandboxBackend 与 agent_sandbox

**SHALL** 依赖 PyPI `agent-sandbox`（`from agent_sandbox import Sandbox`）。

最小实现（继承 `BaseSandbox`）：

| 方法 | 实现 |
|------|------|
| `execute()` | `client.shell.exec_command(command=...)` → `ExecuteResponse` |
| `upload_files()` | `client.file.write_file(...)` |
| `download_files()` | `client.file.read_file(...)` |
| `read/write/grep/glob/ls/edit` | 默认继承 `BaseSandbox`（经 `execute`），首版 **不** 强制重写为 AIO file API |

**SHALL** 对 `(user_id, session_id)` 持有 `threading.Lock`，包裹全部 `execute` 与 `upload/download`（AIO shell 单会话）。

SDK 版本 **SHALL** 与容器镜像 API 版本在 `deploy/` 或 lockfile 中 pin/文档化。

### D5：并发

| 场景 | 策略 |
|------|------|
| 同用户多 session 并行 SSE | **允许**；每 session 独立 AIO 容器 |
| 同 session 并行 subagent / 并行 tool | **允许** 逻辑并行；backend mutex **串行** AIO HTTP 调用 |
| CDP / baoyu skill | 在 session 容器内 `execute`；env `SANDBOX_HEADLESS=1`、`URL_CHROME_PATH`；profile 目录在 `/workspace/.chrome-profile` |
| 未来 browser skill | **MAY** 直接调 `client.browser.*`（同 mutex 或独立 client） |

### D6：生命周期

| 事件 | 动作 |
|------|------|
| 首次需 sandbox | `ensure_session_sandbox(user_id, session_id)` → runner 起容器 → 返回 `base_url` |
| 同 session 再次 run | 复用容器（若 alive） |
| 软删 session | **先** `cancel_task(session_id)` → **`destroy_session_sandbox`** → `delete_session_workspace` |
| idle TTL | 该 session in-flight==0 且超时 → `destroy_session_sandbox`；磁盘保留至删 session |
| shutdown | runner 清理全部 session 沙箱 |

`SandboxService` **SHALL** 维护 per-session in-flight 计数。

### D7：Docker 部署与 bind mount（DooD）

- **`NOESIS_HOST_DATA_DIR`**：宿主机 `.data` 真实路径；runner bind **SHALL** 使用此值。
- compose：runtime 数据卷对齐 backend 与 runner；文档写明映射表。
- 开发：未设时默认 `{REPO_ROOT}/.data`。

### D8：网络

- AIO 容器：**MAY** 允许 HTTPS 公网（Skills、gh、curl）；**SHALL** 使用 dedicated bridge，**SHALL NOT** 直连 backend/MySQL/Qdrant 端口（compose 网络隔离 + 可选 egress 规则）。
- runner：内网 only。
- API：`web_search`/`web_fetch` 仍在 API 进程。

### D9：镜像与密钥

- 默认镜像：`ghcr.io/agent-infra/sandbox:latest`（可配置 `SANDBOX_AIO_IMAGE`）；**MAY** 文档提供国内 mirror。
- API 镜像：无浏览器、无 `agent-sandbox` 运行时依赖（仅 HTTP 客户端在 backend 内）。
- 沙箱 env allowlist：`GH_TOKEN` 等；**SHALL NOT** 注入 `MODEL_API_KEY`、`TAVILY_API_KEY`。

### D10：sandbox-runner API

内网 + `SANDBOX_RUNNER_TOKEN`：

- `PUT /internal/sandboxes/{user_id}/{session_id}` → `{ "base_url": "http://..." }`
- `DELETE /internal/sandboxes/{user_id}/{session_id}`
- `GET /health`

**SHALL NOT** 代理 AIO shell/file 请求（lifecycle only）；backend 直连容器 `base_url`（Docker 内部 DNS 或 runner 返回可达 URL）。

可选 `sandbox_max_replicas`：全局 concurrent session 沙箱上限；LRU evict idle。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| AIO 镜像体积大 | OSS 文档说明；可选预 pull；max_replicas |
| shell 单会话 + mutex 吞吐 | per-session 容器；同 session 串行可接受 |
| SDK/镜像 API 漂移 | pin 版本 + 集成测试 |
| baoyu headless 在容器 | env `SANDBOX_HEADLESS=1` + Chrome docker flags |
| DooD 路径配错 | `NOESIS_HOST_DATA_DIR` + mount 集成测试 |

## Open Questions

- 首版是否 pin `agent-sandbox==0.0.30` 与特定 AIO 镜像 digest。
- `GH_TOKEN`：全局只读 PAT vs 禁用 `gh`。
- 是否在 v2 为 hot path 重写 `read/write` 走 AIO file API（性能优化，非阻塞首版）。
