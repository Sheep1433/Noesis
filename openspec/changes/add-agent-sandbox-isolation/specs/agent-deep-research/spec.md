## MODIFIED Requirements

### Requirement: CompositeBackend 工作区与 Skills 路由

深度研究 **SHALL** 使用 `create_session_sandbox_backend(user_id, session_id)`：

- **默认盘**：`AioSandboxBackend`；Agent virtual **`/`** = AIO 容器 `/workspace`；构建前 `ensure_workspace_dir`；
- **Skills**：AIO 容器 ro `/skills`；CompositeBackend route `/skills/`；
- **SHALL NOT** 使用 `LocalShellBackend`。

#### Scenario: 工作区写入

- **WHEN** session `s1` 写入 `/research/plan.md`
- **THEN** 变更 SHALL 落在 `.../sessions/s1/workspace/research/plan.md`

#### Scenario: session 间隔离

- **WHEN** session `s1` 调用 `execute` 尝试读取 session `s2` 工作区
- **THEN** **SHALL NOT** 成功（独立 AIO 容器 + 最小 mount）

## ADDED Requirements

### Requirement: 深度研究 Web 工具 SHALL 保留在 API 进程

`web_search` / `web_fetch` **SHALL** 在 API 执行；AIO 沙箱 **SHALL NOT** 持有 `TAVILY_API_KEY`。

#### Scenario: CDP 在 session AIO 容器

- **WHEN** 使用 `baoyu-url-to-markdown`
- **THEN** SHALL 在该 session 的 AIO 容器内 `execute`；headless/profile env 由 sandbox 注入
