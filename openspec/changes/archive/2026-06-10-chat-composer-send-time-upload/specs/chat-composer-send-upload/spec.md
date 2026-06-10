## Purpose

本能力规定 chat 页 Composer 在 **方案 B（发送时一并上传）** 下的行为：附件先进入本地队列，用户必须输入有效文字才能发送；发送时依次物化会话、上传附件、发起流式问答。适用于 `COMMON_QA` 及显式启用会话附件 API 的 qa_type。

## ADDED Requirements

### Requirement: 选文件 SHALL 仅进入本地待发送队列

在 `qa_type=COMMON_QA`（及未来显式启用 chat 附件模式的 qa_type）下，用户通过下拉、拖拽或粘贴添加的文件 **SHALL NOT** 在发送前调用 `POST /api/chat/sessions/{session_id}/attachments`。

系统 SHALL 将文件放入 **本地待发送队列**（`PendingComposerFile` 或等价结构），包含至少：`id`（客户端临时标识）、`file`（浏览器 `File`）、`name`、`kind`（`document` | `image`）。

队列项 SHALL 支持用户移除；移除 **SHALL NOT** 调用服务端 DELETE（因尚未上传）。

#### Scenario: 选择文档仅入队

- **WHEN** 用户在智能问答 Composer 选择 `report.pdf`
- **THEN** UI SHALL 展示待发送文件卡片
- **AND** 前端 SHALL NOT 发起附件 upload HTTP 请求

#### Scenario: 粘贴图片仅入队

- **WHEN** 用户在输入框粘贴一张 PNG
- **THEN** 该图片 SHALL 进入本地队列并展示缩略图预览
- **AND** SHALL NOT 调用附件 upload API

#### Scenario: 移除未发送附件

- **WHEN** 用户点击队列中某文件的移除按钮
- **THEN** 该文件 SHALL 从本地队列消失
- **AND** SHALL NOT 产生服务端 API 调用

---

### Requirement: 发送 SHALL 仅在存在有效 user 文字时允许

发送按钮（或等价主发送动作）**SHALL** 在以下任一条件成立时 **禁用（灰化）**：

1. `inputText.trim().length === 0`（空或仅空白字符）；
2. 流式生成进行中且当前交互为「停止」以外的发送（沿用现有 `stylizingLoading` 停止语义）；
3. 发送编排正在执行上传步骤（`isUploadingOnSend === true`）；
4. `qa_type=FAULT_OPERATION_QA` 且本地队列非空（故障运维禁止附件）。

**仅有附件、无有效文字时，发送按钮 SHALL 保持禁用。**

存在有效文字时，本地队列 **MAY** 为空（纯文本发送）或非空（文字 + 附件）。

#### Scenario: 空消息灰化

- **WHEN** 输入框为空或仅含空格/换行
- **THEN** 发送按钮 SHALL 禁用

#### Scenario: 仅有附件无文字灰化

- **WHEN** 本地队列含 1 个文件且输入框 trim 后为空
- **THEN** 发送按钮 SHALL 禁用

#### Scenario: 有文字无附件可发送

- **WHEN** 输入框 trim 后非空且本地队列为空
- **THEN** 发送按钮 SHALL 启用（无其它 qa 级阻塞条件时）

#### Scenario: 有文字有附件可发送

- **WHEN** 输入框 trim 后非空且本地队列含已入队文件
- **THEN** 发送按钮 SHALL 启用（无其它 qa 级阻塞条件时）

---

### Requirement: 点击发送 SHALL 按固定顺序编排 ensure → upload → stream

当用户点击发送且通过 D3 校验后，前端 **SHALL** 按以下顺序执行：

1. **Ensure 会话**：`PUT /api/chat/sessions/{session_id}/ensure`（或等价幂等物化接口），携带 `extra.qa_type`；
2. **上传附件**：对本地队列中每个文件 **串行** 调用 `POST /api/chat/sessions/{session_id}/attachments`；
3. **构建 file_dict**：`{ file_name: "__CHAT_ATTACHMENT__:<attachment_id>" }`；
4. **流式问答**：调用现有 SSE / `exec_query` 入口，传递 `query` 与 `file_dict`；
5. **清理**：全部成功后清空本地队列与 `businessStore.file_list`（发送成功路径与现有一致）。

任一步骤失败 **SHALL** 中止后续步骤（尤其不得在无 file_dict 就绪时启动 stream，除非队列为空）。失败 **SHALL** 向用户展示错误，**SHALL** 保留输入框文字；对已上传成功的附件，首版 **MAY** 保留在 store 供重试（实现细节见 tasks）。

发送过程中 **SHALL** 展示上传/发送 loading 状态（如「上传中…」）。

#### Scenario: 首条消息带附件成功发送

- **WHEN** 新对话中用户输入「总结附件」并附带 1 个已入队 PDF，点击发送
- **THEN** 前端 SHALL 先 ensure 会话
- **AND** SHALL 上传 PDF 获得 `attachment_id`
- **AND** SHALL 以 `file_dict` 哨兵发起流式请求
- **AND** 本地队列 SHALL 清空

#### Scenario: 纯文本发送不调用附件 API

- **WHEN** 用户输入「你好」且本地队列为空，点击发送
- **THEN** SHALL ensure 会话（或 stream 内物化）
- **AND** SHALL NOT 调用附件 upload API
- **AND** SHALL 正常流式返回

#### Scenario: 上传失败中止 stream

- **WHEN** ensure 成功但某一附件 upload 返回 422
- **THEN** 前端 SHALL NOT 启动 SSE stream
- **AND** SHALL 提示该文件失败原因

---

### Requirement: 刷新与新建对话 SHALL 丢弃未发送 Composer 状态

浏览器刷新、执行 `newChat()`、或从当前 editable 会话切换到另一历史会话时，前端 **SHALL** 丢弃：

- 输入框未发送文字；
- 本地待发送附件队列；
- `businessStore.file_list` 中与当前 Composer 相关的 pending 状态。

**SHALL NOT** 使用 sessionStorage / localStorage 恢复上述状态。

因方案 B 下发送前无服务端 upload，刷新 **SHALL NOT** 需要调用附件 DELETE 清理。

#### Scenario: 刷新丢弃队列

- **WHEN** 用户已选文件入队但未发送，随后刷新页面
- **THEN** 本地队列 SHALL 为空
- **AND** 输入框 SHALL 为空（或回到初始 placeholder 状态）

#### Scenario: 新建对话清空

- **WHEN** 用户在有未发送队列时点击「新建对话」
- **THEN** 队列与 file_list SHALL 清空
- **AND** 应生成新的 client `session_id`

---

### Requirement: FAULT_OPERATION_QA SHALL 禁止附件入队或发送

`qa_type=FAULT_OPERATION_QA` 下，系统 **SHALL NOT** 允许附件进入发送流程。

若用户尝试拖拽/粘贴/选择文件，前端 **SHALL** 提示不支持并 **SHALL NOT** 入队（或入队后发送按钮因 D3 规则始终禁用且给出警告）。

#### Scenario: 故障运维拖拽被拒绝

- **WHEN** 用户在故障运维 tab 拖拽文件到 Composer
- **THEN** SHALL 提示暂不支持文件上传
- **AND** SHALL NOT 调用 ensure/upload/stream 附件路径
