# chat-composer Specification

## Purpose

本能力规定聊天 **Composer 产品面**：对话表面生命周期（COMPOSING / SENDING / ACTIVE）、发送时上传、会话附件、`/` `@` mentions、session 级工具开关（MCP / Skills）、以及右侧会话上下文面板。平台 SSE 与落库见 `platform-chat`；设置控制面见 `user-settings`；磁盘布局见 `agent-runtime`。

## Requirements

### Requirement: 对话表面生命周期

系统 SHALL 区分至少：未物化会话的 COMPOSING、发送中的 SENDING、已有 `session_id` 的 ACTIVE。**SHALL NOT** 在用户仅打开空白对话时强制创建持久会话（发送才物化）。

#### Scenario: 发送才物化

- **WHEN** 用户在空白对话输入并发送首条消息
- **THEN** 系统 SHALL 在发送路径创建会话（或等价 ensure），**SHALL NOT** 依赖预先 draft 会话

### Requirement: 发送时上传（方案 B）

对需附件的问答，前端 SHALL 在发送路径：ensure session → 上传附件 → 再发起 stream；**SHALL NOT** 要求用户先手动创建空会话再上传（除非产品显式提供）。

`extra.file_dict` SHALL 为 `Dict[str, str]`（逻辑名 → 会话内相对路径或约定键）。

#### Scenario: COMMON_QA 带文件发送

- **WHEN** 用户在 COMMON_QA 选择文件后发送
- **THEN** 请求到达 stream 前附件 SHALL 已落入该会话 `uploads/` / `attachments/`，且 `file_dict` 可被服务端解析

### Requirement: 会话附件 API

系统 SHALL 提供会话级附件上传/列表/删除（或等价）API；附件原文件与 Markdown 副本路径遵循 `agent-runtime`。附件 TTL / 清理策略若配置，SHALL 在文档与实现一致。

聊天附件作为 Agent 可读上下文时，SHALL 经约定工具或注入路径暴露，**SHALL NOT** 默认 rw 挂载进沙箱 `/workspace`。

#### Scenario: 上传写入会话子树

- **WHEN** 已登录用户向 session `s1` 上传 `a.pdf`
- **THEN** 文件 SHALL 出现在 `.noesis/users/{uid}/sessions/s1/uploads/`（或现行约定目录）

### Requirement: `/` 与 `@` mentions

Composer SHALL 提供 slash / mention 选择器；发送载荷 MAY 含 `mentions`。服务端 SHALL 校验 mentions 并注入 prompt 块；注入给 Agent 的文件路径 SHALL 映射为权威绝对路径（`/workspace/...`、`/skills/...`、`/memory/...`），**SHALL NOT** 把 UI 的 `sessions/{sid}/workspace/...` 原样当作 Agent 路径。

#### Scenario: 文件 mention 映射

- **WHEN** mention 指向 `sessions/{sid}/workspace/notes.md`
- **THEN** 注入块 SHALL 使用 `` `/workspace/notes.md` ``（或 canonicalize 等价结果）

#### Scenario: 非法 mention 拒绝

- **WHEN** mention 指向其它用户或越权路径
- **THEN** 服务端 SHALL 拒绝或忽略该 mention，**SHALL NOT** 泄露越权内容

### Requirement: session 级 MCP / Skills 开关

会话 `extra` SHALL 支持 `mcp_servers` 与 `enabled_skills`（或等价），供 Composer `+` 菜单与 SuperAgent 装配读取。变更 SHALL 持久化到会话，并在后续 run 生效。

#### Scenario: 启用 skill 后可见

- **WHEN** 用户在会话中启用某 personal skill 并发送 SUPER_AGENT 消息
- **THEN** 该 run 的 Skills 过滤 SHALL 包含所选项（在文件存在的前提下）

### Requirement: 会话上下文面板

系统 SHALL 提供会话上下文浏览 API（树：workspace / skills / memory 等）；面板 SHALL 支持只读浏览，并为可内联预览的文本文件（含 Markdown、代码、纯文本）提供**下载**与**编辑保存**操作；Office / PDF 等复杂格式 SHALL 仅通过 artifact URL 打开，不提供内联编辑。

系统 SHALL 提供 `PUT /api/chat/sessions/{session_id}/workspace/file`（Cookie Session 认证 + CSRF，与 `user-platform` 一致），请求体含 `path` 与 `content`，写入范围与只读 API 一致；单文件大小上限与读限制一致（512KB）。

对 `USER.md` / `AGENTS.md`：面板编辑 SHALL 继续可用，作为相对设置页主入口的兼容路径（设置页为产品主入口，见 `user-settings`）；产品文案 MAY 提示用户前往「个人与 Agent 设置」管理画像与偏好。当面板展示用户根下的 `USER.md` 或 `AGENTS.md` 时，UI SHALL 提供「在设置中打开」或等价操作，导航至设置壳对应 `profile` / `memory` section。

认证 SHALL 使用 Session Cookie（见 `user-platform`），**SHALL NOT** 要求 Bearer JWT。

#### Scenario: 树含 workspace 文件

- **WHEN** session workspace 存在 `report.md`
- **THEN** 上下文树 SHALL 暴露对应节点（UI key 可为 `sessions/{sid}/workspace/report.md`）

#### Scenario: 保存工作区 Markdown

- **WHEN** 用户在面板编辑 `sessions/{id}/workspace/report.md` 并点击保存
- **THEN** SHALL 调用 PUT API 写回磁盘，且预览区展示最新内容

#### Scenario: 保存用户记忆文件

- **WHEN** 用户经面板编辑 `USER.md` 或 `AGENTS.md` 并保存
- **THEN** SHALL 调用 PUT API 写回 `users/{user_id}/` 下对应文件，且后续 `/memory/USER.md` 读取可见新内容

#### Scenario: 从面板跳转画像设置

- **WHEN** 用户在上下文树选中 `USER.md` 并触发「在设置中打开」
- **THEN** 系统 SHALL 导航至设置壳且 section 为 `profile`

#### Scenario: 复杂文件无编辑入口

- **WHEN** 用户选中 `.docx` 或 `.pdf`
- **THEN** UI SHALL 打开 artifact 或下载，**SHALL NOT** 展示内联编辑器
