## 1. 沙箱镜像与 runner

- [ ] 1.1 `deploy/sandbox/Dockerfile`：python3、curl、gh、bun、Chromium、bubblewrap、非 root `sandbox`。
- [ ] 1.2 `deploy/sandbox-runner/`：per-user 容器；`PUT/POST/DELETE /internal/sandboxes/{user_id}`；exec body 含 `session_id`；token 鉴权。
- [ ] 1.3 **bwrap exec 隔离**：每次 execute 仅 bind 当前 session workspace + ro `/skills` + ro 系统路径。
- [ ] 1.4 `(user_id, session_id)` exec mutex；命令长度上限。
- [ ] 1.5 egress：HTTPS 公网；拒私网/metadata。
- [ ] 1.6 compose：sandbox-runner、backend env；runner 无公网 ports。
- [ ] 1.7 精简 API Dockerfile（无 Chromium/gh）。

## 2. 配置、路径与 SandboxService

- [ ] 2.1 `SandboxConfig`：runner URL/token、image、timeout、max_output、idle_ttl、max_replicas、gh_token。
- [ ] 2.2 **`NOESIS_HOST_DATA_DIR`** + 对齐 `DATA_DIR`/compose 卷（fix `/.data` vs `/app/data` 分裂）。
- [ ] 2.3 `sandbox_service.py`：`ensure_user_sandbox`、in-flight 计数、destroy（TTL only）。
- [ ] 2.4 CDP：session 派生端口/env，冲突重试。

## 3. DockerSandboxBackend

- [ ] 3.1 `docker_sandbox.py`：BaseSandbox；virtual `/` = session workspace。
- [ ] 3.2 filesystem **跨 session 路径守卫**。
- [ ] 3.3 `create_user_sandbox_backend(user_id, session_id)`。
- [ ] 3.4 切换 deep_research / fault_operation；移除生产 LocalShellBackend。

## 4. 生命周期

- [ ] 4.1 `chat_service.delete_session`：**先 cancel** DEEP_RESEARCH/FAULT run，再 `delete_session_workspace`。
- [ ] 4.2 idle TTL：in-flight==0 才回收用户沙箱。
- [ ] 4.3 `scripts/run.sh dev` + runner；无 Docker 明确报错。
- [ ] 4.4 evals/agent 复用 sandbox backend。

## 5. 测试

- [ ] 5.1 `test_sandbox_security.py`：无 `/app/.env`、无 metadata curl。
- [ ] 5.2 `test_sandbox_session_guard.py`：filesystem + **execute** 均不可跨 session。
- [ ] 5.3 `test_sandbox_exec_mutex.py` 或集成：同 session 并发 exec 不乱序 cwd。
- [ ] 5.4 CDP skill、gh、OpenAlex 集成（可选 mark integration）。
- [ ] 5.5 compose bind：`NOESIS_HOST_DATA_DIR` 写入可读。

## 6. 文档

- [ ] 6.1 `backend/AGENTS.md`：单用户单沙箱、bwrap、virtual `/`、DooD 路径。
- [ ] 6.2 部署 README 卷映射表。
- [ ] 6.3 `openspec validate add-agent-sandbox-isolation`。

## 7. 验证

- [ ] 7.1 pytest 5.x。
- [ ] 7.2 同用户两 session：一容器、两目录、execute 互不可读。
- [ ] 7.3 compose 全栈手动 DEEP_RESEARCH_QA 一轮。
