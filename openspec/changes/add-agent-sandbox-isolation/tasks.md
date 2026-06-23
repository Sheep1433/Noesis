## 1. 依赖与 sandbox-runner（AIO lifecycle，per-user）

- [x] 1.1 `pyproject.toml`：`agent-sandbox`；版本与镜像文档化。
- [x] 1.2 `deploy/sandbox-runner/`：`PUT/DELETE /internal/sandboxes/{user_id}`；返回 `base_url`；`GET /health`。
- [x] 1.3 起 AIO 容器：`users/{uid}/` → `/workspace`（rw）；skills → `/skills`（ro）。
- [x] 1.4 browser env：headless、Chrome 路径、**按 session** profile/CDP 端口。
- [x] 1.5 `sandbox_max_replicas`（用户级）+ per-user idle TTL。
- [x] 1.6 compose + 预 pull 文档。

## 2. 配置与 SandboxService

- [x] 2.1 `SandboxConfig`：runner、镜像、TTL、max_replicas。
- [x] 2.2 `NOESIS_HOST_DATA_DIR` + compose 卷对齐。
- [x] 2.3 `ensure_user_sandbox` / `destroy_user_sandbox`；**per-user** in-flight；缓存 `base_url`。

## 3. AioSandboxBackend

- [x] 3.1 `agent/backends/aio_sandbox.py`：`BaseSandbox` + `agent_sandbox`。
- [x] 3.2 `(user_id, session_id)` mutex；同 user 共享 `base_url`。
- [x] 3.3 `create_user_sandbox_backend`；virtual `/` = `/workspace/sessions/{sid}/workspace`。
- [x] 3.4 切换 deep_research / fault_operation；移除生产 LocalShellBackend。

## 4. 生命周期

- [x] 4.1 `delete_session`：cancel → delete workspace；**不** destroy user sandbox。
- [x] 4.2 idle TTL：user 全 session in-flight==0 才回收。
- [x] 4.3 dev runner + 无 Docker 明确失败。

## 5. 测试

- [x] 5.1 mock `agent_sandbox` + mutex。
- [x] 5.2 无 API 密钥；不可读其它 **用户** workspace。
- [x] 5.3 同用户 execute **MAY** 读 `/workspace/sessions/s2/...`（集成 mark）。
- [x] 5.4 filesystem 默认只写当前 session。
- [x] 5.5 同用户换 session 复用容器（集成 mark）。

## 6. 文档

- [x] 6.1 `backend/AGENTS.md`：AIO + **单用户单沙箱** + 跨 session mount。
- [x] 6.2 部署 README。
- [x] 6.3 `openspec validate`.

## 7. 验证

- [x] 7.1 pytest。
- [x] 7.2 同用户两 session：一容器、两目录、filesystem 不误写。
- [x] 7.3 compose 手动 DEEP_RESEARCH_QA。
