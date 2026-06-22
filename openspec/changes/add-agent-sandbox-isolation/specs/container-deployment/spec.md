## ADDED Requirements

### Requirement: 部署拓扑 SHALL 包含 sandbox-runner 与沙箱镜像

Docker Compose **SHALL** 增加 `sandbox-runner`（内网、Docker socket、不对公网 `ports`）与沙箱镜像（`deploy/sandbox/Dockerfile`）。

`backend` **SHALL** 通过 `SANDBOX_RUNNER_URL`、`SANDBOX_RUNNER_TOKEN` 调用 runner；**SHALL NOT** 挂载 Docker socket。

#### Scenario: backend 不直接 docker run

- **WHEN** 用户发起 `DEEP_RESEARCH_QA`
- **THEN** backend SHALL 经 runner 确保用户沙箱，**SHALL NOT** 在 backend 容器内 `docker run`

### Requirement: API 镜像与沙箱镜像 SHALL 职责分离

API 镜像 **SHALL NOT** 安装 Agent 用 Chromium；沙箱镜像 **SHALL** 含 Chromium、bubblewrap、gh、bun、curl、python3，**SHALL NOT** 含 FastAPI 业务源码树与 `.env`。

#### Scenario: API 容器无 Chromium

- **WHEN** 在 `backend` 容器执行 `which chromium`
- **THEN** SHALL 返回空

### Requirement: runtime 数据卷 SHALL 对齐 agent_workspace 与 DooD bind

Compose **SHALL** 将 runtime 数据（至少含 `agent_workspace`、checkpoints）挂载到 backend 可写路径；**SHALL** 配置 **`NOESIS_HOST_DATA_DIR`** 为 Docker daemon 绑定 mount 使用的 **宿主机**路径。

实现 **SHALL** 消除 backend 容器内 `agent_workspace` 路径与 checkpoint 使用分裂的 layout（例如统一于 `/app/data/` 下，host 侧为同一命名卷）。

#### Scenario: 沙箱 bind 与 backend 写入一致

- **WHEN** Agent 在 compose 环境写入 session workspace 文件
- **THEN** sandbox-runner 绑定的 host 路径 **SHALL** 与 backend 写入路径指向同一 host 存储

#### Scenario: 裸机开发默认 host 路径

- **WHEN** 未设置 `NOESIS_HOST_DATA_DIR` 且非 Docker 部署
- **THEN** bind 源 **SHALL** 默认为 `{REPO_ROOT}/.data`（与 `DATA_DIR` 一致）

### Requirement: sandbox-runner SHALL 提供健康检查

runner **SHALL** 暴露 `GET /health`；compose healthcheck **SHALL** 探测 runner；backend **MAY** 在 runner 不健康时拒绝挂载 sandbox 的 Agent 请求。

#### Scenario: runner 不健康

- **WHEN** runner healthcheck 失败
- **THEN** compose **SHALL** 标记 runner 未就绪；backend 依赖策略 **MAY** 阻止新 sandbox 请求
