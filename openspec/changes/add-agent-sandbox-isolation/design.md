## Context

- **产品决策**：单用户单沙箱；session 级工作区目录不变。
- **审查结论（2026-06）**：整棵 user 树挂载 + 仅 filesystem 守卫 **不够**；`execute` 必须用 per-exec 隔离；须定义并发、DooD 路径、删 session 与 idle TTL。

## Goals / Non-Goals

**Goals:**

- 用户间：独立沙箱容器，不可互读 workspace。
- 同用户 session 间：filesystem 工具 + **execute** 均 **不可** 读写的其它 session 目录。
- Agent 工具路径：virtual `/` = 当前 session workspace（`/research/...` 等 Prompt 不变）。
- 保留 execute / CDP / Skills / API 侧 web 工具。

**Non-Goals:**

- per-session 容器。
- DeerFlow 式 user memory。
- HITL 审批 UI。

## Decisions

### D1：架构

```
backend (API) → sandbox-runner (Docker socket, 内网)
                    → sandbox-{hash(user_id)}  （每用户一容器，长期复用）
                         exec 每次: bwrap 仅见 sessions/{sid}/workspace + /skills
```

### D2：Agent virtual `/` vs 容器物理路径

| 视角 | 路径 |
|------|------|
| Agent `read_file("/research/plan.md")` | virtual `/` = session workspace |
| 容器物理路径 | `/workspace/sessions/{session_id}/workspace/research/plan.md` |
| 宿主机 | `{DATA_DIR}/agent_workspace/users/{uid}/sessions/{sid}/workspace/...` |

`DockerSandboxBackend` 的 `root_dir`（deepagents virtual 根）**SHALL** 对应当前 session workspace，**SHALL NOT** 要求 LLM 在路径中带 `sessions/{sid}/`。

### D3：Filesystem 跨 session 守卫

对 default 盘 `read`/`write`/`grep`/`glob`/`ls`/`edit`：解析后 **SHALL** 拒绝落在当前 session workspace 外的路径（含 `..`、绝对路径指到 `/workspace/sessions/{other}/`）。

### D4：execute 的 session 隔离（关键）

容器 rw 挂载整棵 `users/{uid}/` 仅为 **减少 remount**；**每次 `execute`** runner **SHALL** 使用 **bubblewrap**（或 firejail 等等价）启动子进程，bind：

- `sessions/{session_id}/workspace` → 容器内 exec 根（rw）
- `/skills` → ro
- `/usr`、`/bin` 等运行 CLI 所需路径 → ro

**SHALL NOT** 在未隔离的 shell 中直接 `exec` 到已挂载整棵 user 树的容器全局 namespace。

验证：`execute("cat /workspace/sessions/s2/workspace/x")` 在 session `s1` **SHALL** 失败。

### D5：并发

| 场景 | 策略 |
|------|------|
| 同用户多 session 并行 SSE | **允许**；文件分 session 目录 |
| 同用户 sandbox 容器 | **一个** |
| runner `exec` | **SHALL** 按 `(user_id, session_id)` **mutex**，避免 cwd/环境串线 |
| 同 session 并行 subagent | 共享 session workspace；exec mutex 串行化 shell |
| CDP（baoyu-url-to-markdown） | work 目录在 session workspace；CDP 端口 **`9222 + hash(session_id)%N`** 或 Skill 读 env `SANDBOX_CDP_PORT`；冲突时重试下一端口 |

**不** 在 API 层禁止同用户多 session 并行（除非产品后续要求）。

### D6：生命周期

| 事件 | 动作 |
|------|------|
| 首次需 sandbox | `ensure_user_sandbox(user_id)` |
| 换 session | 复用容器；backend 换 `session_id` + virtual 根 |
| 软删 session | **先** `cancel_task(session_id)`（深度研究/故障运维）；**再** `delete_session_workspace` |
| idle TTL | runner 仅当该 user **无 in-flight Agent run**（全 session 计数为 0）且超过 TTL → `destroy_user_sandbox` |
| shutdown | runner 清理全部用户沙箱 |

`SandboxService` **SHALL** 维护 per-user in-flight 计数（`run_agent` 入口 +1，`finally` -1）。

### D7：Docker 部署与 bind mount（DooD）

**问题**：backend 容器内 `DATA_DIR` 路径 ≠ 宿主机 Docker daemon 所见路径。

**决策**：

- 新增 **`NOESIS_HOST_DATA_DIR`**：宿主机上 `.data`（或 compose 命名卷在 host 上的真实路径）；sandbox-runner bind mount **SHALL** 使用此值。
- 生产 compose：**SHALL** 将 runtime 数据（含 `agent_workspace`）挂载到 backend 与 runner 可一致的 host 路径；**SHALL** 在 `deploy/` 文档写明卷映射表。
- 开发：默认 `{REPO_ROOT}/.data`；`NOESIS_HOST_DATA_DIR` 未设时等于 `DATA_DIR` 解析路径（裸机开发）。

**SHALL** 对齐 checkpoint 与 `agent_workspace` 到同一 runtime 根（例如容器内 `/app/data/`，host 为命名卷），避免 `/.data` 与 `/app/data` 分裂（实现时修正 `common/paths.py` 或 docker 布局，以 spec 验收为准）。

### D8：网络 egress

沙箱容器 egress：允许 TCP 443 公网；拒绝 RFC1918、metadata、`host.docker.internal`、runner/API/MySQL/Qdrant 端口。首版：runner 启动容器时 `--network` + iptables 脚本或 dedicated bridge。

### D9：密钥与镜像

- API 镜像：无 Chromium/gh（Agent 执行面）。
- 沙箱镜像：python3、curl、gh、bun、Chromium、**bubblewrap**。
- 沙箱 env：最小 allowlist；可选 scoped `GH_TOKEN`。
- `web_search`/`web_fetch`：仅 API 进程。

### D10：sandbox-runner 安全

- 内网-only；`SANDBOX_RUNNER_TOKEN` 鉴权。
- `exec` body **SHALL** 含 `session_id`；runner **SHALL** 记录 audit log。
- 可选：`sandbox_max_replicas` 全局 concurrent 用户沙箱上限；超限 LRU  evict idle 容器。
- exec 命令 **SHALL** 限制最大长度（如 32KB）；超时由 `SandboxConfig` 控制。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| bwrap 增加 exec 延迟 | 可接受；安全必需 |
| CDP 端口冲突 | session 派生端口 + 重试 |
| DooD 路径配错 | `NOESIS_HOST_DATA_DIR` + 集成测试 mount 可见性 |
| runner token 泄露 | 内网 + 最小权限 + 审计 |

## Open Questions

- `GH_TOKEN`：全局只读 PAT vs 禁用 `gh`（首版可选 PAT）。
- bubblewrap vs firejail：优先 bubblewrap（Debian slim 易装）。
