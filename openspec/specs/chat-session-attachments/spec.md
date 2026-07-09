## Purpose

本能力规定 Noesis **聊天会话级附件**（含文档与图片）的端到端行为：用户上传的文件经解析或原样存储与会话绑定，由 **ChatAttachmentsMiddleware** 统一消费；**SHALL NOT** 写入 Qdrant 企业 Collection。

上传时机遵循 **`chat-composer-send-upload`**：COMMON_QA 在**发送时** upload，发送前须 **`PUT .../ensure`** 物化会话。
## Requirements
### Requirement: 系统 SHALL 提供会话 ensure 与会话附件上传 API

系统 SHALL 提供幂等 **`PUT /api/chat/sessions/{session_id}/ensure`**：

- Body 可选 `{ "title": string | null, "extra": { "qa_type": string, ... } }`；
- 语义：`ChatService.get_or_create_session`；
- 响应：`ChatSessionResponse`；
- 用于发送编排在上传附件之前物化会话。

系统 SHALL 提供 `POST /api/chat/sessions/{session_id}/attachments`（JWT 认证），接受 multipart 字段 `file`。系统 SHALL 根据 MIME/扩展名区分 `kind=document|image`。

**document** 支持扩展名：`.doc`、`.docx`、`.pdf`、`.txt`、`.xlsx`、`.csv`、`.ppt`、`.pptx`、`.md`；单文件 ≤ `CHAT_ATTACHMENT_MAX_FILE_MB`（默认 20MB）。

单条消息的 `file_dict` 附件数（文档与图片合计）SHALL ≤ `CHAT_ATTACHMENT_MAX_FILES_PER_MESSAGE`（默认 10）。系统 SHALL NOT 对 session 累计附件数设上限。

**image** 支持：`image/jpeg`、`image/png`、`image/webp`、`image/gif`；单文件 ≤ `CHAT_ATTACHMENT_MAX_IMAGE_MB`（默认 5MB）。

**Session 存在性**：

- **上传时**：`session_id` **SHALL** 对应已物化的 `t_chat_session` 记录。若会话不存在，**SHALL** 返回 HTTP 404，**SHALL NOT** 在 upload 接口内隐式创建空会话。
- **越权**：`session_id` 不属于当前用户 **SHALL** 返回 404。

**磁盘布局**：原文件 SHALL 存于 `.data/users/{user_id}/sessions/{session_id}/uploads/`；解析成功的 Markdown 副本 SHALL 存于同会话下 `attachments/`。

#### Scenario: ensure 创建新会话

- **WHEN** 客户端对不存在的 `{session_id}` 调用 ensure，且 JWT 有效
- **THEN** 系统 SHALL 返回 200 及会话详情
- **AND** 数据库 SHALL 存在对应 `t_chat_session`

#### Scenario: ensure 幂等

- **WHEN** 对已存在的同用户会话再次 ensure
- **THEN** 系统 SHALL 返回 200 且不重复插入冲突记录

#### Scenario: 未 ensure 直接 upload

- **WHEN** 对不存在的 `{session_id}` 直接 POST attachments
- **THEN** 系统 SHALL 返回 404「会话不存在」
- **AND** SHALL NOT 写入附件

#### Scenario: 图片上传成功

- **WHEN** 用户在对已 ensure 的本人会话提交合法 PNG
- **THEN** 响应 SHALL 含 `kind=image`、`attachment_id`、`mime_type`
- **AND** 原图 SHALL 存于 `.data/users/{user_id}/sessions/{session_id}/uploads/`

#### Scenario: 文档上传成功

- **WHEN** 已认证用户对已物化会话提交合法文档且解析成功
- **THEN** 系统 SHALL 返回 200，`data` 含 `attachment_id`、`file_name`、`char_count` 与非空 `preview`
- **AND** 系统 SHALL 在磁盘与数据库中持久化附件记录

#### Scenario: 解析失败

- **WHEN** 文件格式不支持或解析后正文为空
- **THEN** 系统 SHALL 返回 HTTP 422 及明确错误信息
- **AND** SHALL NOT 创建有效可引用附件记录

### Requirement: 附件 SHALL 与会话绑定并具备 TTL

每条附件记录 SHALL 关联 `session_id`、`user_id`、`file_name`、`content_path`（或等价存储键）、`char_count`、`created_at`、`expires_at`。

磁盘相对路径 SHALL 相对于 `.data/users/{user_id}/sessions/{session_id}/`。

`expires_at` SHALL 为 `created_at + CHAT_ATTACHMENT_TTL_DAYS`（配置项，默认 7 天）。

系统 SHALL 在读取附件时拒绝已过期记录（HTTP 404 或业务等价）。

#### Scenario: 过期附件不可读

- **WHEN** Agent 或 API 请求读取 `expires_at` 已过的 `attachment_id`
- **THEN** 系统 SHALL 返回附件不存在语义
- **AND** SHALL NOT 将过期正文注入模型

### Requirement: file_dict SHALL 使用 CHAT_ATTACHMENT 引用哨兵

系统 SHALL 定义常量 `CHAT_ATTACHMENT_REF` 前缀（实现名如 `__CHAT_ATTACHMENT__`）。`file_dict` 中值为 `{CHAT_ATTACHMENT_REF}:<attachment_id>` 时，表示以 `key`（file_name）为显示名、以 attachment_id 解析正文。

`file_dict` **SHALL** 在 **全部待发送附件 upload 成功之后**、**SSE stream 启动之前** 由前端组装（见 `chat-composer-send-upload`）。

`resolve_chat_attachments(file_dict, session_id, user_id)`（或等价 Service 方法）SHALL：
- 解析哨兵值并从存储读取正文；
- 对长度超过 800 字符的非哨兵值视为内联正文（兼容旧会话）；
- 对无法解析的条目记录 warning 并跳过，不导致整次请求崩溃。

#### Scenario: 哨兵引用解析成功

- **WHEN** `file_dict` 为 `{"报告.pdf": "__CHAT_ATTACHMENT__:<uuid>"}` 且附件未过期
- **THEN** 解析结果 SHALL 含 `### 报告.pdf\n<正文>`

#### Scenario: 附件 id 与会话不匹配

- **WHEN** `attachment_id` 存在但不属于当前 `session_id`
- **THEN** 系统 SHALL 跳过该条目或返回权限错误，且 SHALL NOT 泄漏其它会话正文

### Requirement: 会话附件 SHALL 不写入 Qdrant 企业 Collection

通过本能力上传的文件 **SHALL NOT** 调用 `QdrantService.upload_document` 写入任意企业知识库 Collection（含 `tmp`）。

#### Scenario: 上传路径隔离

- **WHEN** 客户端在 COMMON_QA 上传会话附件
- **THEN** 系统 SHALL NOT 向 `/api/knowledge_base/collections/*/upload` 等价逻辑写入向量点

### Requirement: 附件模块 SHALL 提供 read_attachment 与 grep_attachment 工具

系统 SHALL 提供 `build_attachment_tools(session_id, user_id)`，返回 `read_attachment` 与 `grep_attachment`（仅 document；**SHALL NOT** 含 `view_image`）。

#### Scenario: 大文件分页读取

- **WHEN** Agent 调用 `read_attachment(path, offset=0, limit=2000)`
- **THEN** 工具 SHALL 返回 Markdown 指定行范围文本

