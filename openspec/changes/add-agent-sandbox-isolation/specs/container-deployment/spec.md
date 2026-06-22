## ADDED Requirements

### Requirement: 部署拓扑 SHALL 包含 sandbox-runner 与 AIO 镜像

Compose **SHALL** 增加 `sandbox-runner`（内网、Docker socket）。

runner **SHALL** 使用 `SANDBOX_AIO_IMAGE`（默认 `ghcr.io/agent-infra/sandbox:latest`）。

backend **SHALL** 经 runner 管理 **per-user** AIO 容器；**SHALL NOT** 挂载 Docker socket。

#### Scenario: backend 不直接 docker run

- **WHEN** 用户发起 `DEEP_RESEARCH_QA`
- **THEN** backend SHALL 经 runner 确保 **用户** AIO 容器

### Requirement: API 镜像与 AIO 镜像 SHALL 职责分离

API 镜像 **SHALL NOT** 安装 Agent 用 Chromium；AIO 镜像 **SHALL NOT** 含 FastAPI 业务源码与 `.env`。backend 镜像 **SHALL** 含 `agent-sandbox` Python 包。

#### Scenario: API 容器无 Chromium

- **WHEN** 在 `backend` 容器执行 `which chromium`
- **THEN** **SHALL** 返回空

### Requirement: runtime 数据卷 SHALL 使用 NOESIS_HOST_DATA_DIR

runner 创建 AIO 容器 bind **SHALL** 使用 `NOESIS_HOST_DATA_DIR` 下的 `agent_workspace/users/{uid}/` → `/workspace`。

#### Scenario: bind 与 backend 一致

- **WHEN** compose 环境 Agent 写入 workspace
- **THEN** host 路径 **SHALL** 与 AIO mount 一致

### Requirement: sandbox-runner SHALL 提供健康检查

runner **SHALL** 暴露 `GET /health`；compose **SHALL** 配置 healthcheck 探测 runner。

#### Scenario: runner 不健康

- **WHEN** runner healthcheck 失败
- **THEN** compose **SHALL** 标记 runner 未就绪

### Requirement: 部署文档 SHALL 说明 AIO 镜像预拉取

部署文档 **SHALL** 说明首次部署前 `docker pull` AIO 镜像（及可选 mirror）。

#### Scenario: 镜像已预拉取

- **WHEN** 镜像已本地存在且 runner healthy
- **THEN** 首用户 sandbox 创建 **SHALL** 在合理超时内返回 `base_url`
