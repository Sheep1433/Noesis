## Why

沙箱写入在真实 Docker Compose / Shell 重定向场景下持续失败，根因不是「双 skills 路径加载」，而是为兼容 AIO、Docker Exec、Local Shell、整用户目录 rw 挂载、虚拟路径重写等需求，把简单挂载做成了多层路径转换系统。现有单测覆盖 `cat /path`，未覆盖 Shell 操作符与 Compose 宿主机路径，无法证明生产可用。需要立即收敛挂载、生命周期与规格，恢复可写、可观测、可回收的沙箱。

## What Changes

- **修复 P0**：`execute` 路径改写不得破坏 `>`、`|`、`&&` 等 Shell 语法；补齐操作符回归测试。
- **修复 P0**：Compose 模式下 runner 向 Docker daemon 传递的 bind 路径必须是宿主机真实路径，禁止把 runner 容器内路径当作宿主机路径（消除 backend volume 与 sandbox 宿主机目录分裂）。
- **修复 P0 / BREAKING**：个人 Skills 改为独立 **ro** mount（`/skills/personal`），不再依赖「整用户目录 rw 挂载 + 工具层只读」；`execute` 不可改写个人 Skills。
- **修复 P1**：backend `_HANDLE_CACHE` 在 runner 返回 404 / 容器不存在时必须失效并自动重建，禁止静默沿用 stale handle。
- **BREAKING**：删除 AIO 兼容链（`aio` backend、agent-sandbox 可选依赖、AIO 端口/浏览器环境、AIO UID=1000 递归 chmod 修补）；只保留 Docker Exec（生产）+ Local Shell（开发/测试）。
- **收敛挂载模型（BREAKING）**：按 session 隔离——仅挂载当前 session workspace → `/workspace`（rw）；公共 skills → `/skills/public`（ro）；个人 skills → `/skills/personal`（ro）。**SHALL NOT** 再把整个 `users/{uid}` 树 rw 挂进容器。
- **收敛路径**：删除 Skills / 虚拟路径的 Shell 级路径重写；Agent 产物优先相对路径；Shell cwd 固定 `/workspace`；用户记忆继续走独立 Middleware/API，**不**暴露给任意 Shell。
- **删除**：`/skills/` 静态索引 backend（若仍存在）；规格中冲突的 `/user-skills/` 与 `/skills/extensions|custom` 双轨命名统一为 `/skills/public` + `/skills/personal`。
- **修复 P2**：个人 Skills 上传/删除后，当前 session 的 `SkillsMiddleware` 元数据须可失效并重扫（或文档化并实现明确失效钩子）。
- **加固（P1）**：sandbox-slim 不以 root 运行；补 CPU/内存/PID/capabilities/只读根文件系统等基础限制（可分阶段，本变更至少消除 root 写宿主机）。

## Capabilities

### New Capabilities

（无）本变更收敛既有运行时能力，不新增独立能力域。

### Modified Capabilities

- `agent-sandbox`：从「每用户 AIO + 整用户目录 rw」改为「Docker Exec / Local Shell + session workspace 隔离 + 双 skills ro 挂载 + handle 失效重建」；删除 AIO 相关需求与「同用户跨 session execute 可读」许可。
- `skills-filesystem`：Agent 侧路径从 `/skills/` + `/user-skills/` 统一为 `/skills/public` + `/skills/personal`；上传 API 写宿主机个人目录，沙箱只读挂载即时可见；补充元数据失效要求。
- `agent-runtime-paths`：与上述挂载/虚拟路径收敛对齐（删除冲突别名与双重重写约定）。
- `container-deployment`：Compose/runner 的 bind 路径语义改为「宿主机路径传 daemon」；删除 AIO 镜像/端口相关部署约定（若规格仍引用）。

## Impact

- **代码**：`backend/agent/backends/path_rewrite.py`、`agent_filesystem.py`、`sandbox_mount_policy.py`、`sandbox_service.py`、factory/backend 选择；`deploy/sandbox-runner/manager.py`、`paths.py`；`deploy/docker-compose.yml`；`deploy/sandbox-slim/Dockerfile`；SkillsMiddleware / skills API。
- **依赖**：移除 `agent-sandbox`（AIO）可选依赖及 AIO 相关配置项。
- **API**：Skills 上传/树接口路径语义不变（仍写 `.data/users/{uid}/skills/`），但 Agent 虚拟路径与沙箱挂载名变更；无新公开 HTTP 前缀。
- **行为 BREAKING**：同用户其它 session / 记忆 / uploads 不再对 Shell 默认可见；依赖 AIO 浏览器 CDP 的 Skills 需另开变更或降级说明。
- **测试**：现有 48 个 sandbox 单测需按新挂载模型调整；**必须**新增 Shell 操作符与 Compose 宿主机路径集成测试。
- **验证**：`cd backend && uv run pytest`（sandbox/skills 相关）；Compose 下真实 slim 容器探针：workspace 写入对 backend/前端可见、公共/个人 skills 可读且个人不可写。
