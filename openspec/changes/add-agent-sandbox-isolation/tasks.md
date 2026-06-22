## 1. 依赖与 sandbox-runner（AIO lifecycle）

- [ ] 1.1 `pyproject.toml`：添加 `agent-sandbox`；pin 版本与 `SANDBOX_AIO_IMAGE` 文档化。
- [ ] 1.2 `deploy/sandbox-runner/`：`PUT/DELETE /internal/sandboxes/{user_id}/{session_id}`；返回 `base_url`；`GET /health`；token 鉴权。
- [ ] 1.3 runner 启动 AIO 容器（默认 `ghcr.io/agent-infra/sandbox:latest`）：仅 mount session workspace→`/workspace`、skills→`/skills`（ro）。
- [ ] 1.4 注入 browser env：`SANDBOX_HEADLESS=1`、`URL_CHROME_PATH`、`BAOYU_CHROME_PROFILE_DIR`。
- [ ] 1.5 `sandbox_max_replicas` + idle session LRU evict（可选首版简化为 TTL only）。
- [ ] 1.6 compose：sandbox-runner、backend env、内部网络；runner 无公网 ports；文档 pre-pull 镜像。

## 2. 配置、路径与 SandboxService

- [ ] 2.1 `SandboxConfig`：runner URL/token、`SANDBOX_AIO_IMAGE`、timeout、idle_ttl、max_replicas、gh_token。
- [ ] 2.2 **`NOESIS_HOST_DATA_DIR`** + 对齐 `DATA_DIR`/compose 卷。
- [ ] 2.3 `sandbox_service.py`：`ensure_session_sandbox`、`destroy_session_sandbox`、per-session in-flight、缓存 `base_url`。

## 3. AioSandboxBackend

- [ ] 3.1 `agent/backends/aio_sandbox.py`：`AioSandboxBackend(BaseSandbox)`；`execute`/`upload_files`/`download_files`。
- [ ] 3.2 `(user_id, session_id)` mutex 包裹全部 AIO HTTP 调用。
- [ ] 3.3 `create_session_sandbox_backend(user_id, session_id)` + `CompositeBackend`（`/skills/` route）。
- [ ] 3.4 切换 `deep_research_agent.py` / `fault_operation_agent.py`；移除生产 `LocalShellBackend`。

## 4. 生命周期

- [ ] 4.1 `chat_service.delete_session`：cancel → `destroy_session_sandbox` → `delete_session_workspace`。
- [ ] 4.2 idle TTL：session in-flight==0 才回收 AIO 容器。
- [ ] 4.3 `scripts/run.sh dev` + runner；无 Docker/runner 明确报错。
- [ ] 4.4 evals/agent 复用 `create_session_sandbox_backend`（或 test double）。

## 5. 测试

- [ ] 5.1 `test_aio_sandbox_backend.py`：mock `agent_sandbox` client；mutex；ExecuteResponse 映射。
- [ ] 5.2 `test_sandbox_security.py`：无 API 密钥注入；mount 最小化（集成或 runner 单测）。
- [ ] 5.3 `test_sandbox_session_isolation.py`：两 session 两容器（集成 mark）。
- [ ] 5.4 baoyu headless smoke（集成 mark optional）。
- [ ] 5.5 compose bind：`NOESIS_HOST_DATA_DIR` 写入可读。

## 6. 文档

- [ ] 6.1 `backend/AGENTS.md`：AIO 路线、per-session 容器、virtual `/`、`agent_sandbox`、DooD。
- [ ] 6.2 部署 README：AIO 镜像 pull、卷映射、runner 拓扑。
- [ ] 6.3 `openspec validate add-agent-sandbox-isolation`。

## 7. 验证

- [ ] 7.1 pytest 5.x。
- [ ] 7.2 同用户两 session：两 AIO 容器、workspace 互不可见。
- [ ] 7.3 compose 全栈手动 `DEEP_RESEARCH_QA` 一轮（含 baoyu 或 web_fetch 路径）。
