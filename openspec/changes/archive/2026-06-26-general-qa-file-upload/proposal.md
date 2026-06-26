## Why

通用问答（`COMMON_QA`）在 chat 页已有文件上传 UI，文件会经 `/api/knowledge_base/collections/tmp/upload` 解析入库，并在流式请求中携带 `file_dict` 元数据；但 **`GeneralQAAgent` 完全忽略 `file_list` 参数**，仅向 LLM 发送纯文本 `HumanMessage`，用户上传的文件对回答无任何影响。此外前端将 `file_list` 数组原样作为 `file_dict` 发送，与后端 `Dict[str, str]` 契约不一致，属于半成品链路。

用户期望获得类似 Gemini / Cursor 的体验：在对话中上传文档（及后续扩展图片），就当前问题或后续多轮追问基于文件内容作答。测试用例生成（`TEST_CASE_QA`）已通过 `resolve_document_context` 实现了 `file_dict` 消费，可作为参考，但不应复用「写入企业知识库 tmp Collection」的路径——那会污染向量库且与「会话级临时附件」语义不符。

## What Changes

- 新增 **会话级附件** 能力：独立上传 API（`POST /api/chat/attachments/upload`），解析文档为 Markdown/文本，按 `session_id` + TTL 存储，返回标准化 `attachment_id`；**不**写入 Qdrant 企业 Collection。
- 统一 **`file_dict` 契约**：`{file_name: attachment_ref}`，其中 `attachment_ref` 为附件 ID 或短文本内联；`COMMON_QA` 与 `platform-chat` 对齐 `TEST_CASE_QA` 已有模式。
- **`ChatAttachmentsMiddleware`（仅 `before_agent`）**：注入文档清单 + multimodal 图片；**不**实现 `before_model` / `view_image`。
- **图片与文档共用** `/api/chat/sessions/{session_id}/attachments` 与 `file_dict` 哨兵；Vision 可用时 `image_url` 注入，否则降级提示。
- **多轮上下文**：同一会话内，用户消息 `extra.file_dict` 持久化附件引用；后续轮次 Agent 可访问本会话历史轮次已上传的附件（通过 session 附件索引或消息元数据聚合）。
- **前端修复与增强**：`FileUploadManager` 改调会话附件 API；发送前将附件列表规范化为 `file_dict`；用户气泡展示附件；`COMMON_QA` 明确启用上传（`FAULT_OPERATION_QA` 继续禁用）。
- **非目标（本 change）**：`view_image` / `before_model`、独立 `/images` API、聊天 OCR、附件跨会话共享。

## Capabilities

### New Capabilities

- `chat-session-attachments`：会话级文件上传、解析、存储、TTL 清理与 `file_dict` 引用解析（供各 `qa_type` 复用）。

### Modified Capabilities

- `agent-common-qa`：`GeneralQAAgent` 必须消费 `file_list`，基于附件内容生成回答；系统提示词补充附件使用策略。
- `platform-chat`：新增附件上传 REST API；流式请求 `extra.file_dict` 契约与持久化；chat 页上传组件与 `file_dict` 序列化对齐后端。

## Impact

- **后端**：新增 `api/chat_attachment_api.py`、`services/chat_attachment_service.py`、schemas；修改 `agent/common_react_agent.py`；可选复用 `kb` 下 `DocumentParser`。
- **前端**：`FileUploadManager.vue`、`chat.vue`、`store/business/index.ts`、`api/chat.ts`；上传 endpoint 从 knowledge_base tmp 切换到 chat attachments。
- **API**：新增 `POST /api/chat/attachments/upload`（需 JWT）；`POST /api/chat/sessions/stream` 的 `extra.file_dict` 语义明确化，**向后兼容**（旧会话无附件时行为不变）。
- **存储**：本地磁盘或 MySQL BLOB/路径表（design 阶段定稿）；与 Qdrant `tmp` Collection 解耦。
- **依赖**：无新 LLM 厂商依赖；复用现有 `DocumentParser` / MarkItDown 解析链。
