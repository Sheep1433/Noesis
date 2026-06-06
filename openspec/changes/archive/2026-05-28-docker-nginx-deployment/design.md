## Context

Noesis 前端开发态通过 Vite 将 `/api` 代理到本机 `127.0.0.1:8089`（见 `frontend/vite.config.ts`）；后端 FastAPI 路由均以 `/api/...` 为前缀（如 `/api/user`、`/api/chat`），系统级 `GET /health` 挂在应用根路径。聊天等能力使用 `StreamingResponse` 与 `text/event-stream`（`backend/api/chat_api.py`）。生产部署需静态站点 + 同源或反向代理下的 API，避免浏览器跨域与混合内容问题。

## Goals / Non-Goals

**Goals:**

- 提供可构建、可运行的 Docker 制品（多阶段或分离镜像），默认由 **Nginx** 对外暴露 HTTP(S)，托管前端 `dist`，并将 `/api`（及根路径健康检查若需要）转发至 Uvicorn 后端。
- Compose 中服务网络与端口约定清晰；后端通过环境变量连接 MySQL、Qdrant 与模型服务，与 `backend/config/env.py` 的 pydantic-settings 一致。
- Nginx 对 SSE 长连接关闭代理缓冲、合理拉长读超时，避免流式对话被网关提前切断。

**Non-Goals:**

- 不在本变更中规定 Kubernetes Helm、多区域高可用或 CI/CD 流水线细节（可后续单独立项）。
- 不修改业务 API 路径前缀或引入 `/api/v2` 等第二套接口。
- 不强制在 compose 内打包生产级 MySQL/Qdrant（允许文档说明「外部已有服务」模式），但若提供示例 compose，应明确数据卷与默认口令仅用于本地演示。

## Decisions

1. **入口形态：Nginx 单入口 + 后端内网**
   - **Rationale**：与常见静态 SPA + API 部署一致；浏览器只访问 Nginx，由 `proxy_pass` 到 `backend:8089`（或 compose 服务名），对齐开发时「前端同源 + `/api`」心智。
   - **Alternatives**：仅 Uvicorn 托管 `StaticFiles` —— 省去一容器但失去成熟网关缓存、TLS 终止与 SSE 调参经验。

2. **路径转发：`location /api/` → 后端**
   - **Rationale**：与现有路由前缀一致；SSE 落在 `/api/chat/...` 时同属该 location，统一设置 `proxy_buffering off`、`proxy_read_timeout` 加大（具体数值在实现 tasks 中给出建议区间）。
   - **Alternatives**：拆分 `location` 仅给 SSE —— 可读性略好但配置重复，首版优先单 `/api` 块 + SSE 相关指令。

3. **前端构建：Node 阶段构建 + Nginx 镜像拷贝 `dist`**
   - **Rationale**：生产镜像不含 dev server；`VITE_*` 在 build 时注入，与 Vite 惯例一致。
   - **Alternatives**：运行时挂载 `dist` —— 适合频繁替换静态资源，首版以「单镜像自包含」为主更易演示。

4. **后端镜像：基于官方 Python slim + `uv sync` 或等价安装，启动 `uvicorn`**
   - **Rationale**：与仓库 `uv` 工作流一致（实现阶段以 `CLAUDE.md` 为准选择 `uv run` 或锁定 `requirements` 导出）。
   - **Alternatives**：distroless —— 镜像更小但调试成本高，可作为后续优化。

5. **配置与密钥**
   - **Rationale**：通过 `env_file` 或 compose `environment` 引用宿主机 `.env.prod`（gitignore），不在镜像层写入真实密钥。
   - **Alternatives**：仅 K8s Secret —— 超出当前 compose 范围。

## Risks / Trade-offs

- **[Risk] SSE 经 Nginx 仍出现中断或缓冲导致延迟** → **Mitigation**：显式关闭 `proxy_buffering`、调大 `proxy_read_timeout`/`send_timeout`，并文档记录与 Uvicorn worker 超时关系。
- **[Risk] MySQL/Qdrant 在 compose 与「外部服务」两种模式下连接串易配错** → **Mitigation**：compose 使用服务名作为 host；文档列出两种拓扑的环境变量示例（无真实密钥）。
- **[Risk] 前端 `base` 与路由 mode（hash/history）影响部署子路径** → **Mitigation**：首版默认根路径部署；若需子路径，在 tasks 中注明需设置 `VITE_ROUTER_MODE` 与 `vite.base` 联动，避免 silent 404。

## Migration Plan

1. 在测试环境构建镜像并 `docker compose up`，用浏览器走完整登录与一次流式对话验证。
2. 将现有手动部署的流量切换到 Nginx 入口（DNS/负载均衡指向新主机）。
3. 回滚：保留旧进程或旧 compose 版本标签，切换回先前入口与镜像 tag。

## Open Questions

- 是否在 compose 中默认附带 MySQL、Qdrant 服务，还是仅 `backend` + `nginx` 两个服务、数据库由运维自备（影响 `tasks.md` 工作量与维护边界）。
- 是否需要 TLS（`certbot` / 用户自带证书）的首版模板，或仅 HTTP 示例由运维在边缘层终止 TLS。
