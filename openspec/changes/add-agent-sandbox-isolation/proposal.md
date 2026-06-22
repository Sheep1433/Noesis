## Why

深度研究（`DEEP_RESEARCH_QA`）与故障运维（`FAULT_OPERATION_QA`）当前通过 `LocalShellBackend` 在 API 宿主机进程内执行 `execute` 与文件系统操作；`execute` 可绕过 `virtual_mode` 读取源码与密钥。多用户场景下须将不可信执行面隔离。

本变更采用 **[AIO Sandbox](https://github.com/agent-infra/sandbox)**（`agent_sandbox` HTTP SDK + all-in-one 容器镜像）：**每 session 一个 AIO 容器**，经 deepagents **`BaseSandbox` 适配层**接入，**不**改 Agent / FilesystemMiddleware / CompositeBackend 栈。磁盘工作区仍 **per-session**；Prompt 路径（virtual `/`、`/skills/`）不变。

## What Changes

- **`agent-sandbox`**：`AioSandboxBackend(BaseSandbox)` + `agent_sandbox` 客户端；`create_session_sandbox_backend(user_id, session_id)`。
- **生命周期**：`sandbox-runner` 为 `(user_id, session_id)` 创建/复用 AIO 容器（默认 `ghcr.io/agent-infra/sandbox`）；仅 mount 当前 session workspace + ro `/skills`；删 session 时 destroy 对应容器。
- **隔离**：用户间、session 间靠 **独立容器 + 最小 mount**；**不**使用 bubblewrap / 自建 docker exec runner。
- **并发**：同 session 内 AIO shell 单会话，backend **SHALL** 对 `(user_id, session_id)` **mutex** 串行 `execute`；不同 session **MAY** 并行。
- **浏览器**：AIO 镜像内置浏览器/VNC/CDP；baoyu 等 Skills 经 `execute`；未来 Skills **MAY** 使用 AIO `browser.*` HTTP API。
- **部署**：`NOESIS_HOST_DATA_DIR`（DooD）；`sandbox-runner` 内网 + token；API 无 Docker socket、无 Chromium。
- 移除生产路径 `LocalShellBackend`；无 Docker/runner 则明确失败。

## Capabilities

### New Capabilities

- `agent-sandbox`：AIO 容器 lifecycle、AioSandboxBackend、session 级隔离、并发锁、idle TTL。

### Modified Capabilities

- `agent-deep-research`、`agent-fault-operation`：沙箱 backend。
- `agent-workspace`：删 session 前 cancel run + destroy session 沙箱 + 删 workspace。
- `container-deployment`：sandbox-runner、AIO 镜像、compose 卷对齐。

## Impact

| 区域 | 影响 |
|------|------|
| `agent/backends/aio_sandbox.py` | `AioSandboxBackend(BaseSandbox)` |
| `services/sandbox_service.py` | session 容器 lifecycle、in-flight、base_url |
| `deploy/sandbox-runner/` | 起停 AIO 容器、mount、health |
| `chat_service.delete_session` | cancel → destroy sandbox → delete workspace |
| `pyproject.toml` | 依赖 `agent-sandbox` |
| `deploy/docker-compose.yml` | runner、AIO 镜像 pull、网络 |
| `deep_research_agent.py` / `fault_operation_agent.py` | 切换 backend 工厂 |
