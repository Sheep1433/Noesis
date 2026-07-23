## ADDED Requirements

### Requirement: 记忆文件节点 SHALL 支持跳转设置入口

当上下文面板展示用户根下的 `USER.md` 或 `AGENTS.md` 时，UI SHALL 提供「在设置中打开」或等价操作，导航至设置壳对应 `profile` / `memory` section。面板内编辑保存行为 SHALL 保持可用（兼容路径）。

#### Scenario: 从面板跳转画像设置

- **WHEN** 用户在上下文树选中 `USER.md` 并触发「在设置中打开」
- **THEN** 系统 SHALL 导航至设置壳且 section 为 `profile`

## MODIFIED Requirements

### Requirement: 上下文面板 SHALL 支持文本文件下载与编辑

面板 SHALL 为可内联预览的文本文件（含 Markdown、代码、纯文本）提供**下载**与**编辑保存**操作；Office / PDF 等复杂格式 SHALL 继续仅通过 artifact URL 打开，不提供内联编辑。

系统 SHALL 提供 `PUT /api/chat/sessions/{session_id}/workspace/file`（Cookie Session 认证 + CSRF，与 `user-auth` 一致），请求体含 `path` 与 `content`，写入范围与只读 API 一致；单文件大小上限与读限制一致（512KB）。

对 `USER.md` / `AGENTS.md`：面板编辑 **SHALL** 继续可用，作为相对设置页主入口的兼容路径；产品文案 MAY 提示用户前往「个人与 Agent 设置」管理画像与偏好。

#### Scenario: 保存工作区 Markdown

- **WHEN** 用户在面板编辑 `sessions/{id}/workspace/report.md` 并点击保存
- **THEN** SHALL 调用 PUT API 写回磁盘，且预览区展示最新内容

#### Scenario: 保存用户记忆文件

- **WHEN** 用户在面板编辑 `AGENTS.md` 或 `USER.md` 并点击保存
- **THEN** SHALL 调用 PUT API 写回 `users/{user_id}/` 下对应文件，且预览区展示最新内容

#### Scenario: 复杂文件无编辑入口

- **WHEN** 用户选中 `.docx` 或 `.pdf`
- **THEN** UI SHALL 打开 artifact 或下载，**SHALL NOT** 展示内联编辑器
