## Why

深度研究（`DEEP_RESEARCH_QA`）与故障运维（`FAULT_OPERATION_QA`）当前通过 `LocalShellBackend` 在 API 宿主机进程内执行 `execute` 与文件系统操作；`execute` 可绕过 `virtual_mode` 读取源码与密钥。多用户场景下须将不可信执行面隔离。

本变更采用 **[AIO Sandbox](https://github.com/agent-infra/sandbox)**（`agent_sandbox` HTTP SDK + all-in-one 容器镜像）：**每 user 一个 AIO 容器**（同用户多 session 复用），经 deepagents **`BaseSandbox` 适配层**接入。磁盘工作区仍 **per-session**；Prompt 默认路径（virtual `/`、`/skills/`）不变；**同用户跨 session 读内容**在容器 mount 层面天然可达（供未来 Skills / execute 使用）。

## What Changes

- **`agent-sandbox`**：`AioSandboxBackend(BaseSandbox)` + `agent_sandbox`；`create_user_sandbox_backend(user_id, session_id)`（session 仅决定 virtual 根与 mutex key）。
- **生命周期**：`sandbox-runner` 为 **`user_id`** 创建/复用 **一个** AIO 容器；rw mount 整棵 `users/{uid}/` → `/workspace`；ro `/skills`；**删 session 不销毁用户沙箱**。
- **隔离**：**用户间**独立容器；**同用户 session 间**共享容器、共享 mount（便于跨 session 访问）；默认 filesystem 工具仍写当前 session virtual `/`。
- **并发**：同用户多 session 可并行 SSE；`AioSandboxBackend` 对 `(user_id, session_id)` **mutex** 串行 AIO HTTP（单 shell 会话）。
- **浏览器**：AIO 镜像内置浏览器；CDP profile/端口 **按 session** 区分。
- **部署**：`NOESIS_HOST_DATA_DIR`；`sandbox-runner` 内网 + token。
- 移除生产路径 `LocalShellBackend`。

## Capabilities

### New Capabilities

- `agent-sandbox`：per-user AIO 容器、AioSandboxBackend、session mutex、跨 session mount、idle TTL。

### Modified Capabilities

- `agent-deep-research`、`agent-fault-operation`：沙箱 backend。
- `agent-workspace`：删 session 前 cancel run；**不** destroy 用户沙箱。
- `container-deployment`：sandbox-runner、AIO 镜像、compose 卷对齐。

## Impact

| 区域 | 影响 |
|------|------|
| `agent/backends/aio_sandbox.py` | `AioSandboxBackend(BaseSandbox)` |
| `services/sandbox_service.py` | per-user 容器 lifecycle、in-flight、base_url |
| `deploy/sandbox-runner/` | 起停 AIO 容器、user 级 mount |
| `chat_service.delete_session` | cancel → delete workspace（保留用户沙箱） |
| `pyproject.toml` | `agent-sandbox` |
