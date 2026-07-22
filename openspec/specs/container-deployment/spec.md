## Purpose

本能力规定 Noesis 使用 Docker 与 Nginx 进行交付部署时的可验收行为：包括服务拓扑、对外端口、网关对 `/api` 与静态前端的转发规则、对 SSE 长连接的网关约束，以及配置与密钥管理原则，便于实现与运维验收对齐且不改变既有业务 API 契约。
## Requirements
### Requirement: 对外入口由 Nginx 提供静态前端与 API 反向代理

部署拓扑 SHALL 使客户端仅通过 Nginx 暴露的 HTTP(S) 端口访问应用；Nginx SHALL 提供前端构建产物（如 `dist`）的静态文件服务，并将路径前缀以 `/api` 开头的请求反向代理至运行 FastAPI 的后端进程，且代理目标与开发环境 Vite 中 `/api` → 后端的行为一致（同源下的 API 前缀）。

#### Scenario: 浏览器请求 REST 接口

- **WHEN** 客户端对同源站点的 `/api/user/login`（或任意已存在的 `/api/...` 业务路径）发起请求
- **THEN** 请求 SHALL 被 Nginx 转发至后端服务并由 FastAPI 处理，且响应与不经 Nginx 直连后端时的语义一致

#### Scenario: 浏览器加载单页应用资源

- **WHEN** 客户端请求非 `/api` 前缀的前端路由或静态资源（如 `index.html`、JS/CSS）
- **THEN** Nginx SHALL 返回对应静态文件或 SPA 回退页面，且不将此类请求错误转发到后端 API 端口

### Requirement: SSE 流式响应在网关层可完整透传

系统 SHALL 在 Nginx 反向代理配置中针对经 `/api` 转发的 SSE（`text/event-stream`）关闭会破坏流式体验的响应缓冲，并设置不小于长对话场景的读超时，以避免网关先于应用结束连接导致客户端收不到完整事件流。

#### Scenario: 用户发起长时间流式对话

- **WHEN** 客户端通过 `/api` 下使用 `StreamingResponse` 与 `text/event-stream` 的端点建立 SSE 消费
- **THEN** Nginx SHALL 持续将后端产生的事件流转发至客户端直至后端正常结束或客户端主动断开，且不因默认短超时或代理缓冲导致整段响应被一次性延迟到连接结束才到达

### Requirement: 容器化运行环境与配置注入

Docker（或 Docker Compose）部署 SHALL 通过环境变量或挂载的 env 文件为后端提供 PostgreSQL 业务库、PostgreSQL LangGraph checkpoint、Qdrant、模型 API 等连接信息，其键名与 `backend/config/env.py` 中 pydantic-settings 字段对应；Compose SHALL 为 PostgreSQL 服务配置持久化存储、健康检查，并使后端在其就绪后启动。镜像与版本控制文件 SHALL NOT 包含真实生产密钥或仅适用于个人机器的硬编码口令。

#### Scenario: 运维使用 PostgreSQL 启动

- **WHEN** 运维在 compose 或运行时环境中设置与后端配置类一致的 PostgreSQL 业务库、LangGraph checkpoint 与 Qdrant 主机、端口及凭证
- **THEN** 后端容器 SHALL 在 PostgreSQL 健康检查就绪后启动并成功连接所配置的外部依赖，且仓库内无可提交的明文密钥变更

### Requirement: 可观测的健康检查

后端 SHALL 保留或暴露与现有应用一致的 `GET /health` 健康检查端点；部署文档或 compose SHALL 说明如何从宿主机或编排侧探测后端存活（可直接访问后端容器端口或通过 Nginx 按需转发），以便自动化重启与发布验证。

#### Scenario: 编排系统探测服务健康

- **WHEN** 监控或编排组件访问文档约定的健康检查 URL
- **THEN** 在后端正常启动且依赖就绪时返回表示健康的响应体，用于判定该实例可接收流量

### Requirement: 部署拓扑 SHALL 包含 sandbox-runner 与 AIO 镜像

Compose **SHALL** 增加 `sandbox-runner`（内网、Docker socket）。

runner **SHALL** 使用 `SANDBOX_AIO_IMAGE`（默认 `ghcr.io/agent-infra/sandbox:latest`）。

backend **SHALL** 经 runner 管理 **per-user** AIO 容器；**SHALL NOT** 挂载 Docker socket。

#### Scenario: backend 不直接 docker run

- **WHEN** 用户发起 `SUPER_AGENT_QA`
- **THEN** backend SHALL 经 runner 确保 **用户** AIO 容器

### Requirement: API 镜像与 AIO 镜像 SHALL 职责分离

API 镜像 **SHALL NOT** 安装 Agent 用 Chromium；AIO 镜像 **SHALL NOT** 含 FastAPI 业务源码与 `.env`。backend 镜像 **SHALL** 含 `agent-sandbox` Python 包。

#### Scenario: API 容器无 Chromium

- **WHEN** 在 `backend` 容器执行 `which chromium`
- **THEN** **SHALL** 返回空

### Requirement: runtime 数据卷 SHALL 使用 NOESIS_HOST_DATA_DIR

runner 创建 AIO 容器 bind **SHALL** 使用 `NOESIS_HOST_DATA_DIR` 下的 `users/{uid}/` → AIO `/workspace`（与 `agent-runtime-paths`、`agent-sandbox` 一致）。

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

