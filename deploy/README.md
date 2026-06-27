# Noesis Docker 部署

生产与开发环境均使用 **Docker Compose**（`deploy/docker-compose.yml`）：nginx + backend + qdrant + sandbox-runner。MySQL 在宿主机或独立实例，通过 `host.docker.internal` 连接（见 `config.docker.yaml`）。

## CI 自动部署

| 分支 | Workflow | GitHub Environment | 端口 | MySQL 库 |
|------|----------|-------------------|------|----------|
| `main` | `.github/workflows/deploy.yml` | `production` | 28468 | `noesis` |
| `dev` | `.github/workflows/deploy-dev.yml` | `development` | 28469 | `noesis_dev` |

**同机双栈**（推荐 zzqroot 场景）：IP 与 SSH 用户相同，用不同 `DEPLOY_PATH` 与端口区分；`development` Environment 仅需覆盖 `DEPLOY_PATH`（如 `/root/zzq/code/noesis-dev`），其余 Secrets 继承仓库级配置。

| 栈 | 目录 | `COMPOSE_PROJECT_NAME` |
|----|------|------------------------|
| 生产 | `/root/zzq/code/noesis` | `noesis` |
| 开发 | `/root/zzq/code/noesis-dev` | `noesis-dev` |

服务器首次准备：

1. 安装 Docker，克隆两份仓库到上表目录（生产跟踪 `main`，开发跟踪 `dev`）。
2. 各目录复制 `deploy/.env.docker.example` → `deploy/.env.docker`；开发栈额外设置 `APP_ENV=dev`、`MYSQL_DATABASE=noesis_dev`。
3. 在 MySQL 创建开发库：`CREATE DATABASE noesis_dev ...`（可与生产共用同一 MySQL 实例）。
4. 预拉 AIO 镜像：`docker pull ghcr.io/agent-infra/sandbox:latest`。
5. 推送对应分支后，CI 通过即自动执行 `scripts/deploy-remote.sh`。

手动触发：Actions 页选择 **Deploy** 或 **Deploy Dev** → **Run workflow**。

> **勿使用**仓库根目录旧版 `docker-compose.yml` 或 `deploy/Dockerfile.backend`；CI 与 `deploy-remote.sh` 仅认 `deploy/docker-compose.yml`。

## 服务拓扑

| 服务 | 说明 |
|------|------|
| `nginx` | 前端静态资源 + 反向代理 |
| `backend` | FastAPI API |
| `qdrant` | 向量库 |
| `sandbox-runner` | 内网 AIO 沙箱 lifecycle（持 Docker socket） |

## 首次部署

1. 复制 `deploy/.env.docker.example` → `deploy/.env.docker` 并填写密钥。
2. **预拉 AIO 镜像**（与 backend `agent-sandbox==0.0.30` 配套）：

```bash
docker pull ghcr.io/agent-infra/sandbox:latest
```

3. 在仓库根目录启动：

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

## 沙箱配置分工

| 配置位置 | 内容 |
|----------|------|
| `backend/config.yaml` → `sandbox.backend` | `aio` / `local_shell` |
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
