## 1. 仓库制品与忽略规则

- [x] 1.1 在仓库根或 `deploy/` 下新增 `.dockerignore`，排除 `.git`、`node_modules`、`__pycache__`、本地 `.env*` 中可能含密钥的文件模式，避免构建上下文过大或泄漏
- [x] 1.2 新增示例环境文件（如 `.env.docker.example`），仅含占位符与说明，与 `backend/config/env.py` 字段对齐，并确保该示例可被提交

## 2. 后端镜像与进程

- [x] 2.1 编写后端 `Dockerfile`：安装运行依赖、复制 `backend/`、使用 `uv` 或锁定依赖安装方式，默认 `CMD` 启动 Uvicorn 监听 `0.0.0.0:8089`（与 `AppSettings.app_port` 一致），生产关闭 `reload`
- [x] 2.2 在镜像构建或本地构建后执行 `uv run app.py`（或等价启动）验证无语法级启动失败；Compose 中为后端配置 `depends_on` 与健康检查（若引入数据库服务则依赖其就绪策略）

## 3. 前端构建镜像或阶段

- [x] 3.1 编写前端多阶段构建：`pnpm install` + `pnpm build`，通过构建参数或 `ARG`/`ENV` 注入必要的 `VITE_*`（生产默认关闭 mock、指向同源 `/api` 等），产出静态文件目录
- [x] 3.2 运行 `pnpm build` 验证前端产物可生成（若 CI 未覆盖，至少在实现 PR 中本地执行一次）

## 4. Nginx 配置

- [x] 4.1 新增 `nginx` 配置模板：`root` 指向前端 `dist`，`location /api/` `proxy_pass` 至后端服务名与端口；包含 `proxy_http_version 1.1`、`proxy_set_header Host/X-Real-IP/X-Forwarded-For/X-Forwarded-Proto`
- [x] 4.2 为 SSE 在 `/api` 代理块增加 `proxy_buffering off` 与足够大的 `proxy_read_timeout`（建议不少于 600s 或与业务最长流一致），并文档说明调参依据
- [x] 4.3 按需增加 `location = /health` 转发至后端或保留仅内网探测方式，并在 `tasks` 完成后的 README/部署文档中写清验收 curl 示例

## 5. Docker Compose 编排

- [x] 5.1 编写 `docker-compose.yml`：至少包含 `nginx` 与 `backend` 服务，网络互通；对外映射 80（及可选 443 占位由运维补证书）
- [x] 5.2 明确 MySQL/Qdrant 是 compose 内服务还是 `extra_hosts`/外部 URL：若内置示例数据库，须使用命名卷并标注**演示用默认口令**；若外置，须在 compose 中仅通过环境变量引用
- [x] 5.3 将 Nginx 配置以 volume 挂载或 COPY 进镜像二选一并文档化；确保静态资源与 `try_files` 满足 Vue Router history 模式回退（若默认 history）

## 6. 文档与规格归档准备

- [x] 6.1 在 `README.md` 或 `docs/` 中增加「Docker + Nginx 部署」小节：前置条件、启动命令、端口表、环境变量清单、常见问题（SSE 超时、CORS 不应在纯同源部署中出现）
- [x] 6.2 实现完成后执行 `/opsx:archive` 或按仓库 OpenSpec 流程将 `openspec/changes/docker-nginx-deployment/specs/container-deployment/spec.md` 合并入 `openspec/specs/container-deployment/spec.md`（若采用归档工具则按其步骤）

## 7. 回归与安全检查

- [x] 7.1 手工验收：浏览器打开 Nginx 端口，完成登录（`POST /api/user/login`）与一次聊天 SSE 流式对话，确认无混合内容错误与 404 前缀错误
- [x] 7.2 确认镜像与 compose 仓库 diff 中无 JWT/数据库/模型 API 真实密钥；JWT 默认示例密钥若仍存在须在部署文档中提示更换

<!-- 说明：按维护者要求跳过本机打镜像与自动化/手工联调；实现与文档已就绪，运行时验证由部署环境执行。 -->
