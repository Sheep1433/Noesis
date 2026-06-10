## MODIFIED Requirements

### Requirement: chat 页 FileUploadManager SHALL 使用会话附件 API

在 `qa_type=COMMON_QA`（及未来显式启用的 qa_type）下，**发送阶段** SHALL 调用 `POST /api/chat/sessions/{session_id}/attachments` 上传本地队列中的文件；**SHALL NOT** 在用户选文件时立即调用该 API。

**SHALL NOT** 对 COMMON_QA 使用 `/api/knowledge_base/collections/tmp/upload`。

发送前 **SHALL** 通过 `PUT /api/chat/sessions/{session_id}/ensure`（或发送编排第一步等价物化）保证 session 已存在，再 upload。

`FAULT_OPERATION_QA` SHALL 继续禁止文件上传。

#### Scenario: COMMON_QA 发送时走附件 API

- **WHEN** 用户在智能问答输入文字并附带本地队列中的文档，点击发送
- **THEN** 前端 SHALL 在 stream 之前请求 `POST /api/chat/sessions/{session_id}/attachments`
- **AND** SHALL NOT 在选文件当下请求该 API

#### Scenario: 选文件时不走 KB tmp

- **WHEN** 用户在 COMMON_QA 选择上传文档
- **THEN** 前端 SHALL NOT 请求 `/api/knowledge_base/collections/tmp/upload`

#### Scenario: 故障运维仍禁止上传

- **WHEN** `qa_type=FAULT_OPERATION_QA` 且用户尝试添加附件
- **THEN** 前端 SHALL 阻止进入可发送状态并提示不支持文件上传

---

### Requirement: 流式请求 extra.file_dict SHALL 为 Dict[str, str]

（保留 `general-qa-file-upload` 要求；补充发送时机构建时机。）

`file_dict` **SHALL** 在 **全部待发送附件 upload 成功之后**、**SSE stream 启动之前** 由前端组装为 `{ file_name: "__CHAT_ATTACHMENT__:<uuid>" }`。

前端 **SHALL NOT** 在附件尚未 upload 完成时启动 stream。

#### Scenario: file_dict 在 upload 完成后发送

- **WHEN** 用户发送带 `notes.pdf` 的消息且 upload 返回 `attachment_id=abc`
- **THEN** stream 请求 `extra.file_dict` SHALL 为 `{"notes.pdf": "__CHAT_ATTACHMENT__:abc"}`
- **AND** SHALL 在 upload 响应之后发起 stream
