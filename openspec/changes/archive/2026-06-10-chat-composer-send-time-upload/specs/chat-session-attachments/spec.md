## MODIFIED Requirements

### Requirement: 系统 SHALL 提供会话附件上传 API

（在 `general-qa-file-upload` 基础上修订会话存在性语义。）

系统 SHALL 提供 `POST /api/chat/sessions/{session_id}/attachments`（JWT 认证），接受 multipart 字段 `file`；可选 Form 字段 `qa_type` 供 ensure 链路写入 session extra。

**Session 存在性**：

- **上传时**：`session_id` **SHALL** 对应已物化的 `t_chat_session` 记录（由发送编排前置 `PUT .../ensure` 或 stream 前物化创建）。若会话不存在，**SHALL** 返回 HTTP 404，**SHALL NOT** 在 upload 接口内隐式创建空会话作为默认产品路径。
- **越权**：`session_id` 不属于当前用户 **SHALL** 返回 404。

系统 **SHALL** 提供幂等 **`PUT /api/chat/sessions/{session_id}/ensure`**：

- Body 可选 `{ "title": string | null, "extra": { "qa_type": string, ... } }`；
- 语义：`ChatService.get_or_create_session`；
- 响应：`ChatSessionResponse`；
- 用于发送编排在上传附件之前物化会话。

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
- **AND** 原图 SHALL 存于会话 `uploads/` 目录

#### Scenario: 文档上传成功

- **WHEN** 已认证用户对已物化会话提交合法文档且解析成功
- **THEN** 系统 SHALL 返回 200，`data` 含 `attachment_id`、`file_name`、`char_count` 与非空 `preview`

#### Scenario: 解析失败

- **WHEN** 文件格式不支持或解析后正文为空
- **THEN** 系统 SHALL 返回 HTTP 422 及明确错误信息
- **AND** SHALL NOT 创建有效可引用附件记录（或 SHALL 标记 failed，不得进入 file_dict）

---

### Requirement: 附件 SHALL 与会话绑定并具备 TTL

（无变更，沿用 `general-qa-file-upload`。）

---

### Requirement: file_dict SHALL 使用 CHAT_ATTACHMENT 引用哨兵

（无变更，沿用 `general-qa-file-upload`；引用在 **发送时 upload 完成后** 由前端写入。）

---

### Requirement: 会话附件 SHALL 不写入 Qdrant 企业 Collection

（无变更。）
