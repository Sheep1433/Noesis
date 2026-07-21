## ADDED Requirements

### Requirement: 设置页为用户记忆主编辑入口

除会话上下文面板外，系统 SHALL 将「个人与 Agent 设置」中的 `profile` / `memory` section（见 `agent-user-settings`）视为用户编辑 `USER.md` 与 `AGENTS.md` 的**主入口**。用户级 memory API 与面板 PUT、Agent `/memory/` **SHALL** 指向同一磁盘文件。

#### Scenario: 设置与 Agent 同盘

- **WHEN** 用户经设置 API 更新 `USER.md` 后 Agent `read_file("/memory/USER.md")`
- **THEN** 返回内容 SHALL 与设置保存内容一致

### Requirement: 记忆分层 L0/L1/L2

系统 SHALL 将用户级记忆分为：

| 层 | 路径（相对 `users/{user_id}/`） | 默认注入会话 |
|----|--------------------------------|--------------|
| L0 画像 | `USER.md` | 是（经 MemoryMiddleware sources） |
| L1 惯例 | `AGENTS.md` | 是 |
| L2 日记 | `memory/YYYY-MM-DD.md` | **否** |

L2 文件 **SHALL NOT** 加入 MemoryMiddleware 的默认 `sources` 列表。L2 目录在启用时由路径模块创建；缺失单日文件时读取 SHALL 视为空或不存在而非 500。

Agent 虚拟路径 SHALL 能访问 L2（建议 `/memory/daily/YYYY-MM-DD.md` 映射至磁盘 `memory/YYYY-MM-DD.md`），具体映射在实现时与 `agent-runtime-paths` 对齐。

#### Scenario: L0/L1 注入而 L2 不注入

- **WHEN** 用户根存在 `USER.md`、`AGENTS.md` 与当日 `memory/YYYY-MM-DD.md`，且 SuperAgent 启动一轮
- **THEN** `<agent_memory>`（或等价注入）SHALL 包含 L0/L1 内容，且 **SHALL NOT** 因默认 bootstrap 整文件注入当日 L2 全文

#### Scenario: 解析日记路径

- **WHEN** 调用获取用户某日日记路径的辅助函数（如 `get_user_daily_memory_path(uid, date)`）
- **THEN** 返回路径 SHALL 等于 `{DATA_DIR}/users/{uid}/memory/{date}.md` 且 `date` 为 `YYYY-MM-DD`

## MODIFIED Requirements

### Requirement: 上下文面板 SHALL 允许用户编辑记忆文件

用户经 `PUT /api/chat/sessions/{session_id}/workspace/file`（见 `chat-session-context-panel`）**SHALL** 仍可读写 `AGENTS.md` 与 `USER.md`；该路径 **SHALL** 与 Agent `/memory/` 及用户设置 memory API 映射同一磁盘文件。

Agent 工具链、设置页与会话面板 **SHALL** 均可读写 `AGENTS.md` 与 `USER.md`。设置页为产品主入口；面板编辑为兼容路径。

#### Scenario: 面板保存 USER.md

- **WHEN** 用户在上下文面板编辑 `USER.md` 并保存
- **THEN** 磁盘 `users/{uid}/USER.md` SHALL 更新，且后续 `MemoryMiddleware` 加载可见新内容

#### Scenario: 面板保存 AGENTS.md

- **WHEN** 用户在上下文面板编辑 `AGENTS.md` 并保存
- **THEN** 磁盘 `users/{uid}/AGENTS.md` SHALL 更新

#### Scenario: 设置保存后面板可见

- **WHEN** 用户经设置页保存 `AGENTS.md` 后在上下文面板打开同一文件
- **THEN** 面板预览 SHALL 显示设置页保存后的内容
