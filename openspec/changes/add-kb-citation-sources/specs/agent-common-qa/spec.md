## ADDED Requirements

### Requirement: COMMON_QA SHALL 挂载 CitationCollector

`qa_service` 在 `qa_type=COMMON_QA` 流式路径 SHALL 创建 `CitationCollector` 并在流结束或异常时释放。

`search_knowledge_base` SHALL 经 collector 登记并为 hit 写入 `citation_index`（`index` 上限 50）。

终态落库前 **SHALL** 调用共享 `_finalize_citations(builder, collector)`（正常 finish、`stop_chat`、disconnect partial 均适用）。

#### Scenario: 检索后 hit 含 citation_index

- **WHEN** Agent 调用 `search_knowledge_base` 且返回至少一条 hit
- **THEN** 工具 JSON 每项 SHALL 含 `citation_index` 为正整数

#### Scenario: stop 路径写入 citations part

- **WHEN** 用户 `/stop` 且 collector 可 finalize 出 items
- **THEN** `stop_chat` 落库内容 SHALL 含 `citations` part

### Requirement: search_knowledge_base hit SHALL 含分片定位字段

有命中时，`hits` 每项 SHALL 在既有字段上增加：

| 字段 | 说明 |
|------|------|
| `shard_id` | 可 retrieve 的 Qdrant point id，或省略/ null（见 `chat-kb-citations`） |
| `chunk_index` | 来自 payload，有则必含 |
| `citation_index` | 回合内引用序号 |

**SHALL NOT** 将裸 `content_hash` 作为 `shard_id` 透出。

#### Scenario: shard_id 来自 point_id

- **WHEN** 检索 metadata 含 `point_id`
- **THEN** 工具 JSON 的 `shard_id` SHALL 等于该值

#### Scenario: 无 shard_id 时仍可返回 hit

- **WHEN** 无法解析 point id
- **THEN** hit SHALL 仍可返回，但 `shard_id` 为 null 或省略
- **AND** `citation_index` SHALL 仍分配（未超上限时）

## MODIFIED Requirements

### Requirement: 系统提示词 SHALL 区分知识库可用与不可用

`GeneralQAAgent` SHALL 根据是否成功挂载知识库工具，动态组装系统提示词：

- **基础角色**：面向企业内部员工的通用问答助手，输出 Markdown，准确、简洁、结构化。
- **知识库可用时**：追加条款要求对涉及企业文档、规范、产品说明、需求等**事实性问题**必须先调用 `search_knowledge_base`；依据工具返回的 `citation_index` 在陈述事实处使用 **`[n]`** 角标（n 为 `citation_index`，1–50）；**SHALL NOT** 在正文中冗长抄写 `collection_name` / `file_name`；检索无相关片段时明确告知「知识库未覆盖该问题」，可结合通用知识并标注不确定性，且 **SHALL NOT** 编造 `[n]`。
- **知识库不可用时**：仅使用基础角色提示词，SHALL NOT 声称已接入企业知识库。

#### Scenario: 向量库可用时提示词含检索指令

- **WHEN** `build_kb_search_tools()` 返回非空工具列表
- **THEN** `system_prompt` SHALL 包含必须先调用 `search_knowledge_base` 与使用 `[n]` 角标的指令

#### Scenario: 向量库不可用时降级为纯 LLM 问答

- **WHEN** Qdrant 未连接或无可检索 Collection
- **THEN** `kb_tools` SHALL 为空列表
- **AND** 系统提示词 SHALL NOT 包含知识库检索强制条款

### Requirement: 工具输出 SHALL 为结构化 JSON 字符串

`search_knowledge_base` 的返回值 SHALL 为 UTF-8 JSON 字符串（`ensure_ascii=False`），语义如下：

| 场景 | 载荷 |
|------|------|
| 向量库未连接 | `{"error": "向量库未连接，无法检索"}` |
| 无可用 Collection | `{"hits": [], "message": "当前无可用知识库 Collection"}` |
| 无命中 | `{"hits": [], "message": "未检索到相关片段"}` |
| 有命中 | `{"hits": [{rank, collection_name, file_name, score, search_mode, header_path, content, shard_id, chunk_index, citation_index}, ...]}` |

`shard_id` 与 `chunk_index` 在无值时 MAY 为 null 或省略键。

#### Scenario: 有命中时的字段完整性

- **WHEN** 检索返回至少一条命中且未超 citation 上限
- **THEN** JSON `hits` 每项 SHALL 含 `collection_name`、`file_name`、`content`、`score`、`citation_index`
- **AND** 若 payload 含 `chunk_index` 则 SHALL 透出
- **AND** 若可解析 point id 则 SHALL 含 `shard_id`

#### Scenario: 无命中时的明确语义

- **WHEN** 全部 Collection 检索后无满足阈值的片段
- **THEN** 工具 SHALL 返回 `hits: []` 与 `message: "未检索到相关片段"`

### Requirement: COMMON_QA 流式路径 SHALL 依赖平台 SSE 基础设施

COMMON_QA 的 `astream_events` 输出 SHALL 经 `LangGraphSseBridge` 与 `AssistantMessageBuilder` 转换为 SSE 并持久化；帧类型含 `citations-available`（snake_case，可选）、`finish`、`[DONE]` 等，规则以 `platform-chat` 与 `chat-kb-citations` 为准。

本能力 **SHALL NOT** 要求 COMMON_QA 发射 `phase-start` / `phase-delta` / `phase-end`。

#### Scenario: 有 KB 引用时发出 citations-available

- **WHEN** finalize 产出非空 items 且 SSE 连接仍可用
- **THEN** 桥接层 SHALL 发出 `citations-available` 并写入 `citations` part

#### Scenario: 工具调用经标准 SSE 透出

- **WHEN** Agent 调用 `search_knowledge_base`
- **THEN** 桥接层 SHALL 发出既有 `tool-input-*` 与 `tool-output-available` 帧

#### Scenario: 用户消息与 assistant 骨架由平台层处理

- **WHEN** 流式连接建立且用户问题非空
- **THEN** user 消息与 assistant 骨架 SHALL 由 `qa_service` 平台逻辑完成
- **AND** `GeneralQAAgent` SHALL NOT 自行写入数据库
