# chat-session-context-panel Specification

## Purpose

本能力规定 chat 页 **会话上下文面板**与配套 API：聚合展示当前会话的 workspace 目录树与未过期附件列表，支持只读查看 workspace 文本文件；不提供编辑/上传/删除 workspace 的 UI（附件删除仍走 composer 与附件 API）。
## Requirements
### Requirement: 系统 SHALL 提供会话上下文聚合 API

系统 SHALL 提供 `GET /api/chat/sessions/{session_id}/context`（JWT 认证），返回当前用户拥有之会话的：

- `tree`：以 `users/{user_id}/` 为根的目录树，包含用户级 `skills/`、`AGENTS.md`、`USER.md`，以及 **仅当前** `sessions/{session_id}/` 下的 `workspace/` 与 `uploads/`（**SHALL NOT** 列出其它会话子目录；**SHALL NOT** 展示 `attachments/` 解析副本以免与 `uploads/` 重复）；
- `session_root_path`：当前会话磁盘根路径（`.data/users/{user_id}/sessions/{session_id}/`）。

`session_id` 不属于当前用户 **SHALL** 返回 HTTP 404。

#### Scenario: 已认证用户拉取上下文

- **WHEN** 用户对本人已物化会话调用 context API
- **THEN** 返回 HTTP 200 且 JSON 含 `tree` 与 `session_root_path`；`tree` 根节点为 `users/{user_id}`，且 `sessions/` 下仅含请求的 `session_id`

#### Scenario: 越权会话

- **WHEN** 用户 A 请求用户 B 的 `session_id`
- **THEN** SHALL 返回 HTTP 404

### Requirement: 系统 SHALL 提供工作区文件只读 API

系统 SHALL 提供 `GET /api/chat/sessions/{session_id}/workspace/file?path={rel}`，读取 `.data/users/{user_id}/` 下相对路径文本文件，允许范围：

- 用户根：`AGENTS.md`、`USER.md`、`skills/**`；
- 当前会话：`sessions/{session_id}/workspace/**`、`sessions/{session_id}/uploads/**`（`attachments/**` 只读副本不通过此 API 暴露）。

`path` 亦 MAY 接受旧版 `workspace/…`、`uploads/…` 前缀并映射至当前 `session_id`。

路径 SHALL 限制在工作区根内，禁止 `..` 穿越；单文件大小上限 SHALL 与 Skills 读文件限制一致（实现默认 512KB）。

#### Scenario: 读取工作区 Markdown

- **WHEN** 用户对本人会话请求存在的 `report.md`
- **THEN** 返回 HTTP 200 且 `content` 为文件 UTF-8 文本

#### Scenario: 路径穿越拒绝

- **WHEN** `path` 含 `../` 或解析后落在 workspace 外
- **THEN** SHALL 返回 HTTP 400 或 404

### Requirement: 对话页 SHALL 展示可折叠右侧上下文面板

`chat.vue`（或等价主对话布局）SHALL 在桌面宽度下提供右侧 `layout-sider`，展示用户目录树（含当前会话 workspace/uploads）；选中叶节点时预览文本内容；`uploads/` 下非文本文件 SHALL 经既有 artifact URL 打开。

面板 SHALL 在 `session_id` 变更时重新加载 context；SHALL 提供手动「刷新」按钮；SHALL 在 SSE 流式 `finish` 事件后自动刷新产物树（允许 debounce）。

#### Scenario: 切换会话更新面板

- **WHEN** 用户在左侧历史列表切换到另一 `session_id`
- **THEN** 右侧面板 SHALL 展示新会话对应的 `sessions/{session_id}/` 子树，用户级 `skills/` 与记忆文件保持不变

#### Scenario: 流式结束后刷新产物

- **WHEN** 当前会话一轮 SSE 输出 `finish` 且 Agent 写入了 workspace 文件
- **THEN** 面板 SHALL 在不整页刷新的情况下更新产物树以包含新文件

### Requirement: 上下文面板 SHALL 支持文本文件下载与编辑

面板 SHALL 为可内联预览的文本文件（含 Markdown、代码、纯文本）提供**下载**与**编辑保存**操作；Office / PDF 等复杂格式 SHALL 继续仅通过 artifact URL 打开，不提供内联编辑。

系统 SHALL 提供 `PUT /api/chat/sessions/{session_id}/workspace/file`（JWT 认证），请求体含 `path` 与 `content`，写入范围与只读 API 一致；单文件大小上限与读限制一致（512KB）。

#### Scenario: 保存工作区 Markdown

- **WHEN** 用户在面板编辑 `sessions/{id}/workspace/report.md` 并点击保存
- **THEN** SHALL 调用 PUT API 写回磁盘，且预览区展示最新内容

#### Scenario: 复杂文件无编辑入口

- **WHEN** 用户选中 `.docx` 或 `.pdf`
- **THEN** UI SHALL 打开 artifact 或下载，**SHALL NOT** 展示内联编辑器

### Requirement: 上下文面板 SHALL 支持目录递归下载

系统 SHALL 提供 `GET /api/chat/sessions/{session_id}/workspace/archive?path={rel}`：**目录**递归打包为 ZIP 下载；**单文件**直接以原始格式下载（不套 ZIP）。路径范围与读写 API 一致，并额外允许 `skills`、`sessions/{id}`、`sessions/{id}/workspace`、`sessions/{id}/uploads` 等目录节点；**SHALL NOT** 允许下载 `users/{user_id}` 根目录。

目录打包时 ZIP 内 SHALL 保留相对子路径；打包内容总大小上限 SHALL 为 20MB。

#### Scenario: 右键下载 workspace 目录

- **WHEN** 用户在文件树对 `sessions/{id}/workspace` 右键选择「下载」
- **THEN** SHALL 下载包含该目录下全部文件的 ZIP

#### Scenario: 右键下载 skills 子目录

- **WHEN** 用户对 `skills/demo-skill/` 右键选择「下载」
- **THEN** SHALL 下载该技能目录的 ZIP，且保留子路径结构

#### Scenario: 右键下载单个文件

- **WHEN** 用户对 `report.md` 右键选择「下载」
- **THEN** SHALL 直接下载原始 `.md` 文件，**SHALL NOT** 额外打包为 ZIP

#### Scenario: 拒绝用户根目录下载

- **WHEN** `path` 为 `users/{user_id}`
- **THEN** SHALL 返回 HTTP 400

