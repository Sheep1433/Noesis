## Why

当前仓库以本地 `pnpm dev` + `uv run app.py` 为主，缺少可复现的一体化部署方式；运维与演示环境需要标准容器编排与反向代理（Nginx）统一入口、静态资源与 API/SSE 转发，降低环境差异与网关配置成本。

## What Changes

- 增加基于 Docker（建议 Docker Compose）的部署描述与可运行制品：`Dockerfile`（前端构建镜像、后端运行镜像）、`docker-compose.yml`（服务定义与网络）。
- 增加 Nginx 作为对外入口：托管前端静态资源（`pnpm build` 产物），并将 `/api`（及项目实际使用的流式路径，如 SSE 相关前缀）反向代理至后端 Uvicorn 服务；配置缓冲、超时以适配长连接 SSE（与现有 `vite` 中 `/api` 代理到 `127.0.0.1:8089` 的行为对齐，**不引入第二套业务 API 前缀**）。
- 通过环境变量或挂载 `.env` 注入 MySQL、Qdrant、DashScope 等密钥与连接信息；**不在镜像或仓库中提交真实密钥**。
- 文档说明：一键启动命令、端口、健康检查与本地开发的差异（可选：MySQL/Qdrant 是否由 compose 一并拉起或仅 `external_links`/外部主机）。

## Capabilities

### New Capabilities

- `container-deployment`：描述 Noesis 使用 Docker 与 Nginx 部署时的拓扑、端口、环境变量约定、网关转发规则及与开发环境代理的一致性要求。

### Modified Capabilities

- （无）本变更为交付与运维侧能力，不改变 `user-auth`、`chat-sessions-and-streaming` 等既有业务规格中的接口契约；网关仅转发既有路径前缀。

## Impact

- 新增/修改文件预计集中在仓库根或 `deploy/`：`Dockerfile*`、`docker-compose*.yml`、`nginx/` 下配置模板、`.dockerignore`、可选 `docs/` 中的部署说明（若任务要求与 PRD 对齐则按 tasks 执行）。
- 依赖：主机需安装 Docker（及 Compose v2）；运行期仍依赖 MySQL、Qdrant 与 LLM 可达网络；后端默认端口 `8089`、前端生产静态由 Nginx 监听 `80`/`443`（具体以 design 为准）。
- **API 兼容**：对外仍通过同一源站下的 `/api/...` 访问 FastAPI，**非 BREAKING**；仅部署方式新增。
