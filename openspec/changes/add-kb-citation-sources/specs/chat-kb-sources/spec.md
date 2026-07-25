## ADDED Requirements

### Requirement: 来源 SHALL 区分 retrieved 与 cited

系统 SHALL 将知识库工具返回且已登记的来源标为 `retrieved`；仅当顶层 assistant 正文包含匹配已登记 `source_ref` 的有效 source token 时，才 SHALL 将该来源标为 `cited`。系统 SHALL NOT 使用 Top-K fallback 将未被正文引用的来源伪装为 cited。

#### Scenario: 没有 source token

- **WHEN** 工具返回三个来源但 assistant 正文没有有效 source token
- **THEN** 三个来源 SHALL 保持 retrieved，引用来源列表 SHALL 为空

#### Scenario: 正文引用一个来源

- **WHEN** 正文包含一个已登记的 `[[source:kb_xxx]]`
- **THEN** 对应来源 SHALL 标为 cited，其余来源 SHALL 保持 retrieved

### Requirement: source_ref SHALL 稳定且不承担授权

harness SHALL 根据规范化 provenance locator 生成稳定 `source_ref`，同一 locator 在同一协议版本下 SHALL 得到相同 token。`source_ref` SHALL 仅用于模型引用和关联，SHALL NOT 被服务端视为访问知识库内容的授权凭据。

#### Scenario: 多次检索重复命中

- **WHEN** 同一轮多个工具调用命中同一规范化 locator
- **THEN** 平台 SHALL 以 source_ref 去重，并保留全部关联 tool_call_id

### Requirement: 来源 SHALL 在 tool end 进入消息构建器

平台 SHALL 在已知知识库工具的 `tool-output-available` 阶段解析 versioned provenance，并将 SourcesPart 写入 `AssistantMessageBuilder`。来源持久化 SHALL NOT 依赖 finish-only contextvar collector。

#### Scenario: 用户停止后刷新

- **WHEN** 知识库工具已结束而用户随后停止生成
- **THEN** partial assistant 消息 SHALL 保留 retrieved/cited 来源，刷新后 SHALL 可回放

#### Scenario: HITL resume 去重

- **WHEN** assistant 从 HITL pending 消息恢复并再次命中相同 source_ref
- **THEN** SourcesPart SHALL 更新既有来源而非追加重复项

### Requirement: 来源详情 SHALL 以 assistant message 为授权根

`GET /api/chat/messages/{message_id}/sources/{source_id}` SHALL 从指定 assistant 消息的 SourcesPart 解析私有 locator，并重新校验当前用户对 session、message 与 collection 的访问权限。客户端 SHALL NOT 通过该 API 自行指定 collection_name 或 shard_id。

#### Scenario: 跨会话猜测 source id

- **WHEN** 用户请求不属于自己的 message/source 组合
- **THEN** 服务端 SHALL 拒绝访问且 SHALL NOT 查询对应 Qdrant point

#### Scenario: 原文已重建

- **WHEN** point id 已失效但 document key 与 chunk index 可定位
- **THEN** 服务端 SHALL 使用受控 fallback 返回片段并标明定位状态

- **WHEN** fallback 也无法精确定位
- **THEN** 服务端 SHALL 返回不可定位状态或 404/410，SHALL NOT 静默返回相似片段

### Requirement: 前端 SHALL 只在展示层生成数字角标

客户端 SHALL 按正文首次出现的有效 source token 顺序映射连续数字角标，并使用同一映射展示 cited 来源列表。持久化 source_id/source_ref SHALL NOT 依赖展示数字。

#### Scenario: 多次检索顺序变化

- **WHEN** 工具返回顺序与正文引用顺序不同
- **THEN** 数字角标 SHALL 按正文首次出现顺序生成且点击 SHALL 打开正确 source_id

### Requirement: 检索来源文案 SHALL 避免误导

前端 MAY 展示未引用的 retrieved 来源，但 SHALL 与 cited 来源分区，并使用“检索来源”或等价低置信文案。系统 SHALL NOT 将 retrieved-only 来源描述为回答依据。

#### Scenario: 仅检索未引用

- **WHEN** assistant 没有有效 source token 但存在 retrieved 来源
- **THEN** UI SHALL 显示零个引用来源，并 MAY 在折叠区显示检索来源
