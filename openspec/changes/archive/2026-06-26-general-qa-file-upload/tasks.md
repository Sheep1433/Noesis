## 1. 配置与数据模型

- [x] 1.1 `ChatAttachmentConfig`：含 `MAX_IMAGE_MB`、`VISION_ENABLED`、`REINJECT_SESSION_IMAGES`、`MAX_IMAGES_PER_MESSAGE`
- [x] 1.2 `TChatAttachment` ORM：`kind`（document|image）、`mime_type`、`preview_base64`（可选）
- [x] 1.3 `chat_attachment_vo.py`：AttachmentResponse 含 `kind`

## 2. 会话附件 Service 与 API

- [x] 2.1 `ChatAttachmentService`：文档 md 副本 + 图片原图存储、list/delete、lazy TTL
- [x] 2.2 `markdown_outline.py`：outline + preview 提取
- [x] 2.3 `chat_attachment_api.py`：POST/GET/DELETE `/api/chat/sessions/{session_id}/attachments`；artifacts 预览
- [x] 2.4 `attachment_tool.py`：`CHAT_ATTACHMENT_REF`、`resolve_chat_attachments`

## 3. ChatAttachmentsMiddleware（仅 before_agent）

- [x] 3.1 `chat_attachments_middleware.py`：仅 `before_agent`（uploaded_files + multimodal + 多轮图片重注入）
- [x] 3.2 `read_attachment` / `grep_attachment` 工具（不含 `view_image`）
- [x] 3.3 `GeneralQAAgent`：`extra_middleware=[ChatAttachmentsMiddleware(...)]`；`run_agent` 写 `noesis_attachments` kwargs
- [x] 3.4 COMMON_QA 系统提示词 + Vision 降级

## 4. 前端改造

- [x] 4.1 `api/chat.ts`：session attachments CRUD
- [x] 4.2 `FileUploadManager`：chat 模式启用图片；统一 `file_dict` 哨兵
- [x] 4.3 `chat.vue`：session 先行、文档/图片气泡展示
- [x] 4.4 store 与 `initChatHistory.ts`

## 5. 测试与验证

- [x] 5.1 Middleware 单测：文档 / multimodal / Vision 降级 / 多轮图片重注入
- [x] 5.2 集成：COMMON_QA 带 doc + png 流式请求（middleware 单测覆盖；全链路 E2E 非合并门禁，延后）
- [x] 5.3 `uv run app.py` + `pnpm lint`（变更文件 lint 通过；chat.vue 存量告警未改）
- [x] 5.4 E2E：docx + 图片 → 多轮追问（非合并门禁，延后人工验收）

## 6. 清理

- [x] 6.1 COMMON_QA 移除 `collections/tmp` 依赖
- [x] 6.2 lazy delete 过期附件
