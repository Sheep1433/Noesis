## Purpose

修正 Compose/runner 的 bind 路径语义：向 Docker daemon 只传宿主机路径；生产沙箱镜像改为 sandbox-slim（非 AIO）；backend 不再依赖 agent-sandbox SDK。

## MODIFIED Requirements

### Requirement: 部署拓扑 SHALL 包含 sandbox-runner 与 sandbox 镜像

Compose **SHALL** 包含 `sandbox-runner`（内网、Docker socket）。

runner **SHALL** 使用配置的 sandbox 镜像（默认 `sandbox-slim` / `SANDBOX_IMAGE`，**SHALL NOT** 默认依赖 `SANDBOX_AIO_IMAGE`）。

backend **SHALL** 经 runner 管理沙箱容器；**SHALL NOT** 挂载 Docker socket。

#### Scenario: backend 不直接 docker run

- **WHEN** 用户发起需要沙箱的 Agent（如 `SUPER_AGENT_QA`）
- **THEN** backend SHALL 经 runner 确保对应 session 沙箱容器

### Requirement: API 镜像与沙箱镜像 SHALL 职责分离

API 镜像 **SHALL NOT** 安装 Agent 用 Chromium；沙箱镜像 **SHALL NOT** 含 FastAPI 业务源码与 `.env`。backend 镜像 **SHALL NOT** 再要求安装 `agent-sandbox` Python 包。

#### Scenario: API 容器无 Chromium

- **WHEN** 在 `backend` 容器执行 `which chromium`
- **THEN** **SHALL** 返回空

### Requirement: runtime 数据卷 SHALL 使用宿主机真实路径

runner 创建沙箱容器时，传给 Docker daemon 的 bind source **SHALL** 为宿主机绝对路径：

- `{NOESIS_HOST_DATA_DIR}/users/{uid}/sessions/{sid}/workspace` → `/workspace`
- 公共 skills 宿主机路径 → `/skills/public`
- `{NOESIS_HOST_DATA_DIR}/users/{uid}/skills` → `/skills/personal`

**SHALL NOT** 将仅存在于 runner 容器内的路径（如未映射到宿主机同路径的 `/data/noesis`）交给 daemon。backend 与沙箱 **SHALL** 读写同一宿主机 workspace 文件。

#### Scenario: bind 与 backend 一致

- **WHEN** compose 环境 Agent 写入 `/workspace/notes.md`
- **THEN** host 上对应 session workspace 文件 **SHALL** 与 backend 读取路径一致

#### Scenario: 禁止容器内路径冒充 host bind

- **WHEN** runner 仅能看到 `/data/noesis` 而宿主机该路径不存在或不等于数据卷
- **THEN** 部署配置 **SHALL** 提供真正的 `NOESIS_HOST_DATA_DIR`（或等价），使 daemon bind 成功且与 backend 同源

### Requirement: 部署文档 SHALL 说明沙箱镜像预拉取

部署文档 **SHALL** 说明首次部署前 `docker pull` / build sandbox-slim（及可选 mirror），**SHALL NOT** 再要求预拉取 AIO 镜像作为默认路径。

#### Scenario: 镜像已预拉取

- **WHEN** sandbox 镜像已本地存在且 runner healthy
- **THEN** 首 session sandbox 创建 **SHALL** 在合理超时内返回执行句柄
