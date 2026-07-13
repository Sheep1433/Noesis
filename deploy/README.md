# Noesis Docker 部署

生产与开发环境均使用 **Docker Compose**（`deploy/docker-compose.yml`）：nginx + backend + PostgreSQL + qdrant + sandbox-runner。PostgreSQL 由 Compose 管理并使用持久化卷。

## CI 自动部署

| 分支 | Workflow | 说明 |
|------|----------|------|
| `main` | `.github/workflows/deploy.yml` | CI 通过后自动部署生产 |

线上 **仅一套** Docker Compose（`:28468`），目录示例 `/root/zzq/code/noesis`。`dev` 分支不自动部署；偶尔线上调试时 SSH 手动执行（见下）。

## 偶尔线上调试 dev

与生产共用同一 compose，仅切换 Git 分支；开发环境应使用独立 Compose 项目名和 PostgreSQL 数据卷：

```bash
cd /root/zzq/code/noesis

# 1. 编辑 deploy/.env.docker
#    APP_ENV=dev          # 可选

# 2. 部署 dev 分支
DEPLOY_BRANCH=dev bash ./scripts/deploy-remote.sh

# 3. 调试结束后恢复生产
#    改回 APP_ENV=prod
DEPLOY_BRANCH=main bash ./scripts/deploy-remote.sh
```

服务器首次准备：

1. 安装 Docker，克隆仓库到 `DEPLOY_PATH`（如 `/root/zzq/code/noesis`）。
2. 复制 `deploy/.env.docker.example` → `deploy/.env.docker`，设置 `POSTGRES_PASSWORD`。
3. 预拉 AIO 镜像：`docker pull ghcr.io/agent-infra/sandbox:latest`。
4. `main` 推送且 CI 通过后自动部署。

> **勿使用**仓库根目录旧版 `docker-compose.yml`；CI 与 `deploy-remote.sh` 仅认 `deploy/docker-compose.yml`。

## 服务拓扑

| 服务 | 说明 |
|------|------|
| `nginx` | 前端静态资源 + 反向代理 |
| `backend` | FastAPI API |
| `postgres` | 业务数据与 LangGraph checkpoint |
| `qdrant` | 向量库 |
| `sandbox-runner` | 内网沙箱 lifecycle + docker exec 代理（持 Docker socket） |

## 首次部署

1. 复制 `deploy/.env.docker.example` → `deploy/.env.docker` 并填写密钥。
2. **构建 slim 沙箱镜像**（默认 `sandbox.backend: docker`）：

```bash
docker build -t noesis/sandbox-slim:latest -f deploy/sandbox-slim/Dockerfile .
```

若仍用全量 AIO（`sandbox.backend: aio` + `SANDBOX_RUNTIME=aio`）：

```bash
docker pull ghcr.io/agent-infra/sandbox:latest
```

3. 在仓库根目录启动：

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

首次部署或发版时，后端容器启动会自动执行 Alembic 迁移（`server.py` → `init_database()`）。Compose 会在空数据卷上创建业务库和 LangGraph checkpoint 库；本地非 Compose 环境使用 `cd backend && uv run python sql/initialize_postgresql.py`。

### DeepDoc 模型（知识库 PDF 解析）

知识库入库使用 RAGFlow DeepDoc，ONNX 权重 **不包含在镜像内**。Compose 已将 `noesis_data` 卷挂载到容器 `/data/noesis`；`deploy/config.docker.yaml` 中：

```yaml
kb:
  deepdoc:
    model_dir: /data/noesis/rag/res/deepdoc
```

**首次部署**在宿主机或容器内下载权重（需网络访问 HuggingFace，或设置 `HF_ENDPOINT`）：

```bash
# 宿主机（推荐：写入 noesis_data 卷对应目录）
mkdir -p /var/lib/docker/volumes/noesis_noesis_data/_data/rag/res/deepdoc
cd backend && uv run python -m kb.download_models /path/to/above/dir

# 或进入 backend 容器
docker compose -f deploy/docker-compose.yml exec backend \
  uv run python -m kb.download_models /data/noesis/rag/res/deepdoc
```

CPU 即可运行；macOS 开发机解析 PDF 需 `brew install libomp`（xgboost 依赖）。详见 `backend/kb/README.md`。

## 沙箱配置分工

| 配置位置 | 内容 |
|----------|------|
| `backend/config.yaml` → `sandbox.backend` | `docker`（推荐）/ `aio` / `local_shell` |
| `backend/config.yaml` → `sandbox.runner_url` | backend 访问 runner 地址 |
| `deploy/docker-compose.yml` → `sandbox-runner` 环境变量 | 镜像、回收、挂载卷等 runner 运维参数 |
| `.env` → `SANDBOX_RUNNER_TOKEN` | runner 与 backend 共享 Bearer token（可选） |

## 本地开发

**推荐**（自动启动 Qdrant、sandbox-runner、前后端）：

```bash
./scripts/run.sh dev
```

也可单独启动后端（`sandbox.backend=aio` 时会自动尝试拉起 runner，路径自动对齐仓库 `.data/` 与 `extensions/skills`）：

```bash
cd backend && uv run app.py
```

`local_shell` 模式无需 Docker / runner：

```yaml
# backend/config.yaml
sandbox:
  backend: local_shell
```
