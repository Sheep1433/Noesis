## Why

深度研究（`DEEP_RESEARCH_QA`）与故障运维（`FAULT_OPERATION_QA`）当前通过 `LocalShellBackend` 在 API 宿主机进程内执行 `execute` 与文件系统操作；`execute` 可绕过 `virtual_mode` 读取源码与密钥。多用户场景下须将不可信执行面隔离。

本变更落地 **per-user 单容器沙箱**（工作区磁盘仍 **per-session**），并补齐：**filesystem 跨 session 守卫**、**execute 的 session 级 exec 隔离（bubblewrap 等）**、**并发与 idle 回收规则**、**Docker bind mount 宿主机路径（DooD）**。

## What Changes

- **`agent-sandbox`**：每 `user_id` 一容器；`create_user_sandbox_backend(user_id, session_id)`；Agent 可见 virtual `/` = 当前 session workspace（Prompt 路径不变）。
- **exec 隔离**：runner 每次 `execute` 在 **session 级 mount namespace** 内运行（仅 bind 当前 session workspace + ro `/skills` + 系统只读），**禁止** shell 读取同用户其它 session 目录。
- **并发**：同用户多 session 可并行 SSE；runner 对 `(user_id, session_id)` 的 exec **SHALL** 串行或 session 级锁；CDP Skill **SHALL** 按 session 分配端口/work 目录。
- **生命周期**：删 session 前先 **cancel** 该 session 进行中 Agent run，再 `delete_session_workspace`；**不**销毁用户沙箱；idle TTL 在 **无 in-flight run** 时回收用户容器。
- **部署**：`NOESIS_HOST_DATA_DIR`（DooD 宿主机路径）；统一 runtime 数据卷与 `agent_workspace` 挂载源；sandbox-runner 内网 + token。
- 移除生产路径 `LocalShellBackend`；无 Docker 则明确失败。

## Capabilities

### New Capabilities

- `agent-sandbox`：per-user 容器、session exec 隔离、路径守卫、并发/TTL、runner、镜像。

### Modified Capabilities

- `agent-deep-research`、`agent-fault-operation`：沙箱 backend。
- `agent-workspace`：删 session 前 cancel run；磁盘 session 级。
- `container-deployment`：sandbox-runner、`NOESIS_HOST_DATA_DIR`、数据卷对齐。

## Impact

| 区域 | 影响 |
|------|------|
| `sandbox_service.py`、`docker_sandbox.py` | 核心实现 |
| `chat_service.delete_session` | 先 cancel 再删 workspace |
| `qa_service` / `BaseAgent` | in-flight 计数供 idle TTL |
| `common/paths.py` 或 env | Docker 下 DATA_DIR 与 host bind 对齐 |
| `deploy/` | runner、沙箱镜像、compose 卷 |
