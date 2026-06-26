# chat-session-context-panel Specification

## Purpose

本能力规定 chat 页 **会话上下文面板**与配套 API：聚合展示当前会话的 workspace 目录树与未过期附件列表，支持只读查看 workspace 文本文件；不提供编辑/上传/删除 workspace 的 UI（附件删除仍走 composer 与附件 API）。
## Requirements
### Requirement: 系统 SHALL 提供会话上下文聚合 API

系统 SHALL 提供 `GET /api/chat/sessions/{session_id}/context`（JWT 认证），返回当前用户拥有之会话的：

- `workspace`：工作区目录树（结构与 Skills 树节点类似：`key`、`label`、`isLeaf`、`children`）；
- `attachments`：未过期附件列表（复用 `AttachmentResponse` 或等价字段）；
- `workspace_root_exists`：布尔，表示 `workspace/` 是否已在磁盘创建。

`session_id` 不属于当前用户 **SHALL** 返回 HTTP 404。

#### Scenario: 已认证用户拉取上下文

- **WHEN** 用户对本人已物化会话调用 context API
- **THEN** 返回 HTTP 200 且 JSON 含 `workspace` 与 `attachments` 字段

#### Scenario: 越权会话

- **WHEN** 用户 A 请求用户 B 的 `session_id`
- **THEN** SHALL 返回 HTTP 404

### Requirement: 系统 SHALL 提供工作区文件只读 API

系统 SHALL 提供 `GET /api/chat/sessions/{session_id}/workspace/file?path={rel}`，读取 `.data/users/{user_id}/sessions/{session_id}/workspace/` 下相对路径文本文件。

路径 SHALL 限制在工作区根内，禁止 `..` 穿越；单文件大小上限 SHALL 与 Skills 读文件限制一致（实现默认 512KB）。

#### Scenario: 读取工作区 Markdown

- **WHEN** 用户对本人会话请求存在的 `report.md`
- **THEN** 返回 HTTP 200 且 `content` 为文件 UTF-8 文本

#### Scenario: 路径穿越拒绝

- **WHEN** `path` 含 `../` 或解析后落在 workspace 外
- **THEN** SHALL 返回 HTTP 400 或 404

### Requirement: 对话页 SHALL 展示可折叠右侧上下文面板

`chat.vue`（或等价主对话布局）SHALL 在桌面宽度下提供右侧 `layout-sider`，包含：

- Tab「产物」：展示 `workspace` 树；选中叶节点时预览文本内容；
- Tab「附件」：展示本会话附件列表；点击可预览或打开已有 artifact URL。

面板 SHALL 在 `session_id` 变更时重新加载 context；SHALL 提供手动「刷新」按钮；SHALL 在 SSE 流式 `finish` 事件后自动刷新产物树（允许 debounce）。

#### Scenario: 切换会话更新面板

- **WHEN** 用户在左侧历史列表切换到另一 `session_id`
- **THEN** 右侧面板 SHALL 展示新会话的 workspace 与 attachments

#### Scenario: 流式结束后刷新产物

- **WHEN** 当前会话一轮 SSE 输出 `finish` 且 Agent 写入了 workspace 文件
- **THEN** 面板 SHALL 在不整页刷新的情况下更新产物树以包含新文件

### Requirement: 上下文面板首版 SHALL 为只读

面板 **SHALL NOT** 提供编辑、上传或删除 workspace 文件的 UI；附件删除 **SHALL** 继续仅通过既有附件 API 与 composer 流程，不在面板首版强制实现删除按钮。

#### Scenario: 无写操作入口

- **WHEN** 用户在产物 Tab 预览某文件
- **THEN** UI SHALL 仅展示内容与下载/复制，**SHALL NOT** 提供保存到磁盘服务端的编辑控件

