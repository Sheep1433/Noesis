## ADDED Requirements

### Requirement: 流式请求 extra.file_dict SHALL 为 Dict[str, str]

`POST /api/chat/sessions/stream` 及 `QaQueryRequest.file_dict` SHALL 要求类型为 `Dict[str, str]`：键为原始文件名（显示用），值为 `__CHAT_ATTACHMENT__:<uuid>` 哨兵或内联正文。

前端 chat 页 **SHALL NOT** 将 `file_list` 数组原样作为 `file_dict` 发送。

user 消息持久化时 `extra.file_dict` SHALL 保存上述字典格式，以便历史会话恢复。

#### Scenario: 前端发送规范 file_dict

- **WHEN** 用户在 COMMON_QA 上传 `notes.pdf`（attachment_id=`abc`）并发送问题
- **THEN** 流式请求 `extra.file_dict` SHALL 为 `{"notes.pdf": "__CHAT_ATTACHMENT__:abc"}`
- **AND** 持久化的 user 消息 extra SHALL 含相同结构

#### Scenario: 历史消息恢复附件展示

- **WHEN** 客户端加载含 `extra.file_dict` 的 user 消息
- **THEN** chat 页 SHALL 在用户气泡旁展示附件文件名列表（与现有 `FileListItem` 行为一致）

### Requirement: chat 页 FileUploadManager SHALL 使用会话附件 API

在 `qa_type=COMMON_QA`（及未来显式启用的 qa_type）下，`FileUploadManager` SHALL 调用 `POST /api/chat/attachments/upload` 而非 `/api/knowledge_base/collections/tmp/upload`。

上传 SHALL 在有效 `session_id` 存在后进行；若用户在新会话首条消息前上传，前端 SHALL 先创建或绑定会话再上传。

`FAULT_OPERATION_QA` SHALL 继续禁止文件上传（保留现有 `checkAllFilesUploaded` 警告逻辑）。

#### Scenario: COMMON_QA 上传走附件 API

- **WHEN** 用户在智能问答 tab 选择上传文档
- **THEN** 前端 SHALL 请求 `/api/chat/attachments/upload` 且携带当前会话 `session_id`

#### Scenario: 故障运维仍禁止上传

- **WHEN** `qa_type=FAULT_OPERATION_QA` 且存在待上传文件
- **THEN** 前端 SHALL 阻止发送并提示不支持文件上传

### Requirement: 会话附件 API SHALL 注册于平台路由

`chat_attachment_api` router SHALL 在 `api/__init__.py` 导出并在 `server.py` `controller_list` 登记；路径前缀 SHALL 为 `/api/chat/attachments`（或与现有 chat API 一致的 `/api/chat` 子路径）。

响应 SHALL 使用 `ResponseUtil` 封装。

#### Scenario: 路由可发现

- **WHEN** 应用启动且模块已注册
- **THEN** OpenAPI SHALL 列出 `POST /api/chat/attachments/upload`
