## ADDED Requirements

### Requirement: GeneralQAAgent SHALL 经 ChatAttachmentsMiddleware（仅 before_agent）消费附件

`GeneralQAAgent` SHALL 通过 `create_noesis_agent(..., extra_middleware=[ChatAttachmentsMiddleware(...)])` 挂载会话附件中间件。

中间件 **SHALL 仅实现 `before_agent`**；SHALL NOT 实现 `before_model`、`view_image` 工具或 `viewed_images` state。

`run_agent` SHALL 将 `file_list`、`session_id`、`user_id` 写入末条 `HumanMessage.additional_kwargs.noesis_attachments`。

#### Scenario: 有文档时注入 uploaded_files

- **WHEN** `file_dict` 含 document 类型附件
- **THEN** `before_agent` SHALL 注入 `<uploaded_files>` 块
- **AND** Agent 工具集 SHALL 含 `read_attachment` 与 `grep_attachment`

#### Scenario: 有图片且 Vision 可用

- **WHEN** `file_dict` 含 image 附件且 Vision 可用
- **THEN** `before_agent` SHALL 将 HumanMessage.content 转为含 `image_url` 的 list
- **AND** 用户问题 SHALL 出现在 text 块中

#### Scenario: 多轮追问历史图片

- **WHEN** 用户在首轮上传图片、第二轮仅文本追问且 `CHAT_ATTACHMENT_REINJECT_SESSION_IMAGES=true`
- **THEN** `before_agent` SHALL 再次注入该会话未过期图片的 `image_url` 块（不超过 `MAX_IMAGES_PER_MESSAGE`）

#### Scenario: 无附件时 Middleware 无操作

- **WHEN** `file_dict` 为空且会话无历史附件
- **THEN** Middleware SHALL 不修改 HumanMessage

### Requirement: COMMON_QA 系统提示词 SHALL 含附件使用策略

当本轮或会话存在可用附件时，系统提示词 SHALL 追加：附件优先于知识库；大文档先 `read_attachment`；有图片且 Vision 可用时依据消息内图片回答。

#### Scenario: 有附件时的提示词

- **WHEN** 会话存在已解析 document 或 image 附件
- **THEN** `system_prompt` SHALL 含附件优先条款

### Requirement: COMMON_QA 多轮 SHALL 可访问会话历史文档

即使用户本轮未重传文档，`before_agent` SHALL 在 `<uploaded_files>` 列出历史 document；Agent SHALL 通过 `read_attachment` 读取。

#### Scenario: 后续轮次引用已上传文档

- **WHEN** 首轮上传 `A.pdf`，第二轮 `file_list` 为空并提问文档内容
- **THEN** Agent SHALL 能通过 `read_attachment` 获取正文并作答
