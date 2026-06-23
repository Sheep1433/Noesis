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

## 沙箱相关环境变量

| 变量 | 说明 |
|------|------|
| `NOESIS_HOST_DATA_DIR` | 宿主机数据根（compose 卷 `noesis_data`）；runner bind `users/{uid}/` → 容器 `/workspace` |
| `SANDBOX_RUNNER_URL` | backend 访问 runner 的内网地址（compose 默认 `http://sandbox-runner:8090`） |
| `SANDBOX_RUNNER_TOKEN` | runner 与 backend 共享 Bearer token |
| `SANDBOX_AIO_IMAGE` | AIO 容器镜像（默认 `ghcr.io/agent-infra/sandbox:latest`） |
| `SANDBOX_MAX_REPLICAS` | 并发用户沙箱上限 |
| `SANDBOX_IDLE_TTL_SECONDS` | 用户全 session idle 后回收容器 |

## 本地开发（sandbox-runner）

```bash
# 终端 1：runner（需 Docker）
export NOESIS_HOST_DATA_DIR="$(pwd)/.data"
export SANDBOX_SKILLS_HOST_DIR="$(pwd)/extensions/skills"
python deploy/sandbox-runner/main.py

# 终端 2：backend
cd backend && uv run app.py
```

未启动 runner 或无 Docker 时，深度研究 / 故障运维 Agent 将返回明确 sandbox 失败，**不会**回退 `LocalShellBackend`。
