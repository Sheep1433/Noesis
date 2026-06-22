## MODIFIED Requirements

### Requirement: CompositeBackend 工作区与 Skills 路由

深度研究 **SHALL** 使用 `create_user_sandbox_backend(user_id, session_id)`：

- **默认盘**：`DockerSandboxBackend`；Agent virtual **`/`** = 当前 session workspace；构建前 `ensure_workspace_dir`；
- **Skills**：沙箱内 ro `/skills`；
- **SHALL NOT** 使用 `LocalShellBackend` 或 per-session 容器。

#### Scenario: 工作区写入

- **WHEN** session `s1` 写入 `/research/plan.md`
- **THEN** 变更 SHALL 落在 `.../sessions/s1/workspace/research/plan.md`

#### Scenario: execute 受 session 隔离

- **WHEN** session `s1` 调用 `execute` 尝试读取 session `s2` 工作区
- **THEN** **SHALL NOT** 成功（bwrap + 路径守卫）

## ADDED Requirements

### Requirement: 深度研究 Web 工具 SHALL 保留在 API 进程

`web_search` / `web_fetch` **SHALL** 在 API 执行；沙箱 **SHALL NOT** 持有 `TAVILY_API_KEY`。

#### Scenario: CDP 在用户沙箱

- **WHEN** 使用 `baoyu-url-to-markdown`
- **THEN** SHALL 在用户沙箱内 execute，端口按 session 分配
