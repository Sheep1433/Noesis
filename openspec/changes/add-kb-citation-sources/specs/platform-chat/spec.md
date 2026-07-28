## ADDED Requirements

### Requirement: 平台聊天 SHALL 持久化 typed citations 与 retrieval results

assistant `content.parts` 的顶层 `text` part SHALL 支持可选 `annotations[]`；知识库引用 annotation SHALL 包含 `type=kb_citation`、message-scoped `citation_id`、左闭右开的 `start_index/end_index`、document/version/segment identity、展示元数据与 verification 状态。系统 SHALL 以独立 `retrieval` part 持久化工具检索结果，SHALL NOT 将 retrieved results 与 cited annotations 合并为同一状态列表。

#### Scenario: 一条正文只引用部分检索结果

- **WHEN** retrieval part 有三个 results，而 typed answer 只绑定其中一个 evidence
- **THEN** text annotations SHALL 只包含被绑定且校验通过的 citation
- **AND** 其余两个 result SHALL 仅保留在 retrieval part

#### Scenario: 历史消息无新字段

- **WHEN** 客户端加载不含 annotations 或 retrieval part 的旧 assistant 消息
- **THEN** 客户端 SHALL 继续显示纯文本和既有 parts，不得解析失败

### Requirement: Annotation offset SHALL 确定且可验证

`start_index/end_index` SHALL 基于所属原始 text part `content` 的 Unicode code point 计算，并使用左闭右开区间。平台 SHALL 仅持久化非空、未越界且 evidence identity 与本轮 retrieval manifest 一致的 annotation；无效 binding SHALL NOT 通过文本猜测修复。

#### Scenario: Binding 指向未知 evidence

- **WHEN** typed answer segment 引用未出现在本轮 retrieval manifest 的 evidence id
- **THEN** 平台 SHALL 丢弃该 citation binding 并保留正文
- **AND** SHALL NOT 选择 Top-K result 替代

### Requirement: Citation SSE SHALL 使用独立 annotation patch

`/api/chat` 流 SHALL 支持 snake_case 的 `retrieval-results-available` 与 `text-annotation-added` 事件。文本仍由现有 `text-delta` 交付；annotation added SHALL 携带 `text_part_id` 与完整 annotation，且引用范围对应的文本必须已进入消息构建器。未知事件对旧客户端 SHALL 为可忽略的向后兼容扩展。

#### Scenario: 正常流式生成

- **WHEN** COMMON_QA 检索后生成一个带有效 evidence binding 的 answer segment
- **THEN** 系统 SHALL 先登记 retrieval results，再交付正文与对应 annotation patch
- **AND** segment streaming 模式下，完成 segment 的 annotation SHALL 最迟在对应 `text-end` 前进入 builder

#### Scenario: Provider 仅在终态返回 structured answer

- **WHEN** provider 只能在完整正文生成后返回可校验的 typed segments
- **THEN** 系统 MAY 在 `text-end` 后登记 annotation
- **AND** annotation SHALL 最迟在 `finish` 与终态持久化之前进入 builder
- **AND** `text-end` SHALL 只结束正文字符交付，不得封闭 text part 的 annotation 集合

#### Scenario: 客户端断连

- **WHEN** 客户端在收到 annotation patch 前断开
- **THEN** PersistSink SHALL 继续根据 builder 权威快照持久化已校验 annotation 与 retrieval part
- **AND** SHALL NOT 依赖客户端收到该事件
