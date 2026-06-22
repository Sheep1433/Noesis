## MODIFIED Requirements

### Requirement: CompositeBackend 工作区与 Skills 路由

深度研究 **SHALL** 使用 `create_user_sandbox_backend(user_id, session_id)`：

- **默认盘**：`AioSandboxBackend`；virtual **`/`** = 当前 session workspace；**用户级** AIO 容器；
- **Skills**：ro `/skills`；
- **SHALL NOT** 使用 `LocalShellBackend` 或 per-session 容器。

#### Scenario: 工作区写入

- **WHEN** session `s1` 写入 `/research/plan.md`
- **THEN** 变更 SHALL 落在 `.../sessions/s1/workspace/research/plan.md`

#### Scenario: 同用户换 session 复用容器

- **WHEN** 用户 `u1` 从 session `s1` 切换到 `s2`
- **THEN** **SHALL** 复用同一 AIO 容器；filesystem 默认盘 **SHALL** 指向 `s2` workspace

## ADDED Requirements

### Requirement: 深度研究 Web 工具 SHALL 保留在 API 进程

`web_search` / `web_fetch` **SHALL** 在 API 执行；AIO 沙箱 **SHALL NOT** 持有 `TAVILY_API_KEY`。

#### Scenario: CDP 在用户沙箱

- **WHEN** 使用 `baoyu-url-to-markdown`
- **THEN** SHALL 在用户 AIO 容器内 `execute`；profile/端口按 session 区分

#### Scenario: 跨 session 研究（未来）

- **WHEN** Agent 需引用同用户其它 session 产出
- **THEN** **MAY** 经 `execute` 读取 `/workspace/sessions/{other_sid}/workspace/...` 而 **SHALL NOT** 要求新建容器
