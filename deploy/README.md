# Noesis Docker 部署

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
