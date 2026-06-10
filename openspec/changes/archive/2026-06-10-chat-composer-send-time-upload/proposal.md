## Why

`general-qa-file-upload` 已实现会话级附件 API 与 Agent 消费链路，但前端仍采用 **「选文件即上传」**（方案 A）：附件在发送前调用 `POST /api/chat/sessions/{id}/attachments`，而 `session_id` 仅在首条消息发送时才写入数据库。首次对话先上传会导致 **「会话不存在」404**，且与用户产品规则冲突：

- **不允许只上传不提问**（无有效 user 文字不得发送）；
- **刷新页面丢弃未发送内容**（不做草稿恢复）；
- **发送按钮**在消息为空、纯空格、或仅有附件无文字时 **灰化**。

当前文档体积不大，发送时串行上传 + 解析的等待可接受。应统一为 **方案 B：点击发送时一并上传**，会话在 **首次发送** 时物化，避免孤儿会话/孤儿附件。

## What Changes

- **Composer 发送编排**：用户选文件仅进入 **本地待发送队列**；点击发送且通过校验后，按序 `get_or_create_session` → 上传附件 → 构建 `file_dict` → 发起 SSE 流式请求。
- **发送按钮启用规则**：`trim(text).length > 0` 为必要条件；有未完成本地文件或上传/解析失败时 SHALL 禁用或阻塞发送。
- **刷新/新建对话**：清空输入框、本地附件队列、`businessStore.file_list`；**不**恢复 sessionStorage 草稿；**不**调用 DELETE 清理（因服务端尚未上传）。
- **会话 ID**：继续由 `chat.vue` 前端生成 `uuids[qa_type]`；**不**改用 `POST /api/chat/sessions` 服务端生成 ID 作为主路径。
- **附件 API 语义**：上传 API 仍要求 session 已存在（由发送编排先物化）；**撤销**「上传时 lazy 物化空会话」作为产品路径（实现层可在 send 流程内物化后立即 upload）。
- **非目标**：发送前随时上传（方案 A）、刷新恢复草稿、只上传不提问、大文件后台预上传（混合方案）。

## Capabilities

### New Capabilities

- `chat-composer-send-upload`：chat 页 Composer 本地附件队列、发送时上传编排、发送按钮灰化规则、刷新丢弃策略。

### Modified Capabilities

- `platform-chat`：修订 `FileUploadManager` 与 `chat.vue` 上传时机；发送前不得调用附件 upload API。
- `chat-session-attachments`：明确上传前 session 须已物化；物化触发点为 **首条 user 消息发送流程**，而非独立上传动作。

## Impact

- **前端**：`FileUploadManager.vue`、`chat.vue`、`store/business/index.ts`；移除/禁用选文件后立即 `uploadSessionAttachment`；`handleCreateStylized` 增加 upload-then-stream 编排。
- **后端**：发送链路已有 `get_or_create_session`；附件 upload API 保持 strict session 校验即可（无需 upload 端 lazy create 作为默认产品行为）。
- **Spec 冲突**： supersede `general-qa-file-upload` 中 platform-chat「有效 session 存在后再上传」的 **发送前上传** 表述，改为 **发送时上传**。
