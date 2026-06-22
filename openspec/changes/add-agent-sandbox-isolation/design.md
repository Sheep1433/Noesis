## Context

- **产品决策**：**单用户单 AIO 沙箱**；磁盘工作区 **per-session**；同用户 **MAY** 跨 session 访问 workspace（mount 整棵 `users/{uid}/`）。
- **架构约束**：deepagents `BackendProtocol` + `FilesystemMiddleware` + `CompositeBackend`；**不**引入 DeerFlow `Sandbox` ABC。
- **AIO 约束**：容器内 shell **单持久会话**；对 `(user_id, session_id)` mutex 串行 HTTP 调用。

## Goals / Non-Goals

**Goals:**

- **用户间**：独立 AIO 容器，不可互读 workspace。
- **同用户**：**一个** AIO 容器；rw mount `users/{uid}/` → `/workspace`；便于未来跨 session 读/汇总。
- Agent 默认 virtual **`/`** = **当前** session workspace（Prompt `/research/...` 不变）；**`/skills/`** ro。
- `AioSandboxBackend` + `agent_sandbox`；`execute` **MAY** 访问容器内 `/workspace/sessions/{other_sid}/...`（同用户）。
- `sandbox-runner` 持 Docker socket；API 直连 AIO `base_url`。

**Non-Goals:**

- per-session 容器（已废弃，容器过多且阻碍同用户数据共享）。
- bubblewrap / 自建 docker exec runner。
- DeerFlow `/mnt/user-data/...` 路径体系。
- 首版强制 filesystem 工具跨 session 读（默认仍 confine 到当前 session virtual `/`；跨 session 优先 `execute` 或后续专用能力）。

## Decisions

### D1：架构

```
backend (API)
  → sandbox-runner (Docker socket, 内网)
       → aio-{hash(user_id)}              # 每用户一容器
            :8080 HTTP (agent_sandbox SDK)
            mount: users/{uid}/ → /workspace
  → AioSandboxBackend(BaseSandbox)       # session_id 决定 virtual 根 + mutex
  → CompositeBackend(default + /skills/)
  → FilesystemMiddleware (unchanged)
```

### D2：Agent virtual `/` vs AIO 容器物理路径

| 视角 | 路径 |
|------|------|
| Agent `read_file("/research/plan.md")` | virtual `/` = **当前** session workspace |
| AIO 容器内（当前 session 文件） | `/workspace/sessions/{sid}/workspace/research/plan.md` |
| 同用户其它 session（execute 可达） | `/workspace/sessions/{other_sid}/workspace/...` |
| Skills | `/skills/...`（ro） |
| 宿主机 | `{DATA_DIR}/agent_workspace/users/{uid}/sessions/{sid}/workspace/...` |

`AioSandboxBackend` **SHALL** `virtual_mode=True`，`root_dir` = 容器内 `/workspace/sessions/{session_id}/workspace`。

### D3：隔离与跨 session 策略

| 边界 | 策略 |
|------|------|
| 用户 ↔ 用户 | **独立 AIO 容器**；**SHALL NOT** mount 其它 `users/{uid}/` |
| 同用户 session ↔ session | **共享容器** + **共享** `/workspace` mount |
| filesystem 默认盘（virtual `/`） | **SHALL**  confine 到**当前** session workspace（避免误写其它 session） |
| `execute` | **MAY** 读写 `/workspace/sessions/*` 下任意 session 目录（同用户） |
| 未来跨 session Skill | **MAY** 基于同一 mount 或 `execute` 实现，无需新容器 |

**SHALL NOT** mount API `/app`、`.env`、其它用户目录、附件、checkpoint。

### D4：AioSandboxBackend 与 agent_sandbox

**SHALL** 使用 `agent-sandbox` SDK；继承 `BaseSandbox`：

| 方法 | 实现 |
|------|------|
| `execute()` | `client.shell.exec_command` |
| `upload_files()` / `download_files()` | AIO file API（路径在容器内绝对路径） |
| 其它文件 op | 默认 `BaseSandbox`（经 `execute`） |

**SHALL** 对 `(user_id, session_id)` **mutex**（同用户容器内多 session 并行时仍安全）。

同一 `user_id` 的 backend 实例 **SHALL** 共享同一 `base_url`（同一 AIO 容器）。

### D5：并发

| 场景 | 策略 |
|------|------|
| 同用户多 session 并行 SSE | **允许**；**一个** AIO 容器 |
| AIO shell 调用 | 按 `(user_id, session_id)` **mutex** 串行 |
| CDP / baoyu | profile：`/workspace/sessions/{sid}/workspace/.chrome-profile`；端口按 session env |
| 未来 browser API | 同用户共享容器；mutex 按 session |

### D6：生命周期

| 事件 | 动作 |
|------|------|
| 首次需 sandbox | `ensure_user_sandbox(user_id)` → runner 起容器 → `base_url` |
| 换 session | **复用**同一容器；backend 换 `session_id` + virtual 根 |
| 软删 session | cancel → `delete_session_workspace`；**SHALL NOT** `destroy_user_sandbox` |
| idle TTL | 该 user **全部 session** in-flight==0 且超时 → `destroy_user_sandbox` |
| shutdown | runner 清理全部用户沙箱 |

`SandboxService` **SHALL** 维护 **per-user** in-flight 计数（任意 session run 期间 +1）。

### D7：Docker 部署（DooD）

- **`NOESIS_HOST_DATA_DIR`**：runner bind 使用宿主机真实路径。
- mount：`{host}/agent_workspace/users/{uid}/` → `/workspace`（rw）。

### D8：网络 / D9：镜像密钥 / D10：runner API

同前；runner lifecycle API 改为 **user 级**：

- `PUT /internal/sandboxes/{user_id}` → `{ "base_url": "..." }`
- `DELETE /internal/sandboxes/{user_id}`
- `GET /health`

`sandbox_max_replicas`：**concurrent 用户沙箱**上限。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 同容器多 session 误写 | filesystem virtual `/` 仍 confine 当前 session |
| AIO shell mutex 吞吐 | 用户少可接受；按 session 锁而非 per-user 全局锁 |
| 跨 session execute 误操作 | Prompt/Skill 约定；未来可加显式 cross-session 工具 |
| 镜像体积 × 用户数 | max_replicas + idle TTL；OSS 文档 pre-pull |

## Open Questions

- 是否在 v2 为 filesystem 增加「只读跨 session」virtual 路径前缀。
- `GH_TOKEN` scoped PAT vs 禁用 `gh`。
