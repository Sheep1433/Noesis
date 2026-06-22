## ADDED Requirements

### Requirement: 部署拓扑 SHALL 包含 sandbox-runner 与 AIO 镜像

Docker Compose **SHALL** 增加 `sandbox-runner`（内网、Docker socket、不对公网 `ports`）。

runner **SHALL** 拉取/使用可配置 AIO 镜像（默认 `ghcr.io/agent-infra/sandbox:latest`，env `SANDBOX_AIO_IMAGE`）。

`backend` **SHALL** 通过 `SANDBOX_RUNNER_URL`、`SANDBOX_RUNNER_TOKEN` 管理 session 沙箱 lifecycle；**SHALL** 通过 `agent_sandbox` 连接 runner 返回的 `base_url`；**SHALL NOT** 挂载 Docker socket。

#### Scenario: backend 不直接 docker run

- **WHEN** 用户发起 `DEEP_RESEARCH_QA`
- **THEN** backend SHALL 经 runner 确保 session AIO 容器，**SHALL NOT** 在 backend 容器内 `docker run`

### Requirement: API 镜像与 AIO 沙箱镜像 SHALL 职责分离

API 镜像 **SHALL NOT** 安装 Agent 用 Chromium 或 AIO all-in-one 栈；AIO 镜像 **SHALL** 由 runner 单独拉取运行，**SHALL NOT** 含 Noesis FastAPI 源码与 `.env`。

backend 镜像 **SHALL** 含 `agent-sandbox` Python 包（HTTP 客户端）。

#### Scenario: API 容器无 Chromium

- **WHEN** 在 `backend` 容器执行 `which chromium`
- **THEN** SHALL 返回空

### Requirement: runtime 数据卷 SHALL 对齐 agent_workspace 与 DooD bind

Compose **SHALL** 将 runtime 数据（至少含 `agent_workspace`、checkpoints）挂载到 backend 可写路径；**SHALL** 配置 **`NOESIS_HOST_DATA_DIR`** 为 Docker daemon bind 使用的 **宿主机**路径。

runner 创建 AIO 容器时 **SHALL** 用 `NOESIS_HOST_DATA_DIR` 解析 session workspace 的 host bind 源。

#### Scenario: AIO bind 与 backend 写入一致

- **WHEN** Agent 在 compose 环境写入 session workspace 文件
- **THEN** runner 绑定的 host 路径 **SHALL** 与 backend 写入路径指向同一 host 存储

#### Scenario: 裸机开发默认 host 路径

- **WHEN** 未设置 `NOESIS_HOST_DATA_DIR` 且非 Docker 部署
- **THEN** bind 源 **SHALL** 默认为 `{REPO_ROOT}/.data`（与 `DATA_DIR` 一致）

### Requirement: sandbox-runner SHALL 提供健康检查

runner **SHALL** 暴露 `GET /health`；compose healthcheck **SHALL** 探测 runner；backend **MAY** 在 runner 不健康时拒绝挂载 sandbox 的 Agent 请求。

#### Scenario: runner 不健康

- **WHEN** runner healthcheck 失败
- **THEN** compose **SHALL** 标记 runner 未就绪；backend **MAY** 拒绝新 sandbox 请求

### Requirement: 文档 SHALL 说明 AIO 镜像预拉取

部署文档 **SHALL** 说明首次部署前 `docker pull` AIO 镜像（及可选国内 mirror），避免首请求超时。

#### Scenario: 首次 Agent 请求

- **WHEN** 镜像已预拉取且 runner healthy
- **THEN** 首 session sandbox 创建 **SHALL** 在合理超时内返回 `base_url`
