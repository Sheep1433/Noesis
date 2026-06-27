# Noesis Docker 部署

生产与开发环境均使用 **Docker Compose**（`deploy/docker-compose.yml`）：nginx + backend + qdrant + sandbox-runner。MySQL 在宿主机或独立实例，通过 `host.docker.internal` 连接（见 `config.docker.yaml`）。

## CI 自动部署

| 分支 | Workflow | 说明 |
|------|----------|------|
| `main` | `.github/workflows/deploy.yml` | CI 通过后自动部署生产 |

线上 **仅一套** Docker Compose（`:28468`），目录示例 `/root/zzq/code/noesis`。`dev` 分支不自动部署；偶尔线上调试时 SSH 手动执行（见下）。

## 偶尔线上调试 dev

与生产共用同一 compose，**只换 Git 分支 + MySQL 库名**（数据隔离）：

```bash
cd /root/zzq/code/noesis

# 1. 编辑 deploy/.env.docker
#    MYSQL_DATABASE=noesis_dev
#    APP_ENV=dev          # 可选

# 2. 部署 dev 分支
DEPLOY_BRANCH=dev bash ./scripts/deploy-remote.sh

# 3. 调试结束后恢复生产
#    改回 MYSQL_DATABASE=noesis、APP_ENV=prod
DEPLOY_BRANCH=main bash ./scripts/deploy-remote.sh
```

首次调试前在 MySQL 创建开发库：`CREATE DATABASE noesis_dev ...`（可与生产共用同一 MySQL 实例）。

服务器首次准备：

1. 安装 Docker，克隆仓库到 `DEPLOY_PATH`（如 `/root/zzq/code/noesis`）。
2. 复制 `deploy/.env.docker.example` → `deploy/.env.docker`，生产默认 `MYSQL_DATABASE=noesis`。
3. 预拉 AIO 镜像：`docker pull ghcr.io/agent-infra/sandbox:latest`。
4. `main` 推送且 CI 通过后自动部署。

> **勿使用**仓库根目录旧版 `docker-compose.yml`；CI 与 `deploy-remote.sh` 仅认 `deploy/docker-compose.yml`。

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
