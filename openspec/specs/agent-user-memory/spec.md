# agent-user-memory Specification

## Purpose
TBD - created by archiving change add-agent-user-settings. Update Purpose after archive.
## Requirements
### Requirement: 设置页为用户记忆主编辑入口

系统 SHALL 将设置页作为 USER.md 与 AGENTS.md 的主编辑入口，并仅提供 Markdown 原文编辑。设置 API、上下文面板与 Agent `/memory/` SHALL 指向同一磁盘文件。系统 SHALL NOT 为 USER.md 维护固定“常用字段”区块或第二套字段保存接口。

#### Scenario: 编辑 USER.md 原文
- **WHEN** 用户在设置页保存 USER.md Markdown 原文
- **THEN** Agent 后续读取 SHALL 获得同一内容

#### Scenario: 不展示固定画像字段
- **WHEN** 用户打开 USER.md 设置页
- **THEN** 页面 SHALL 直接展示原文预览或编辑器且不展示称呼、时区、语言、角色固定表单

### Requirement: 记忆分层 L0/L1/L2

系统 SHALL 将用户级记忆分为：L0 `USER.md` 稳定画像、L1 `AGENTS.md` 长期惯例、L2 `memory/YYYY-MM-DD.md` 按日整理的跨会话记忆。L0/L1 可按 Agent profile 注入，L2 SHALL NOT 默认注入，且 SHALL 只通过列表、检索或来源追溯按需读取。系统 SHALL NOT 因做梦任务自动修改 L0/L1。

#### Scenario: L2 不默认注入
- **WHEN** 用户存在 L0、L1 与多日 L2 文件并启动 SuperAgent
- **THEN** 默认上下文 SHALL NOT 包含任一 L2 文件全文

#### Scenario: 做梦不改长期规则
- **WHEN** 系统完成每日记忆整理
- **THEN** USER.md 与 AGENTS.md 的内容 SHALL 保持不变

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
