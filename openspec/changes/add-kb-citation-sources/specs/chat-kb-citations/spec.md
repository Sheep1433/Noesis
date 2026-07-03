## Purpose

本能力规定 Noesis 聊天场景中**知识库检索结果的结构化引用**：从 `search_knowledge_base` 命中登记为带序号的 Citation、经 **text part** 正文 `[n]` 角标解析得到 cited 子集、通过 SSE（snake_case）与 `content.parts` 持久化，并在 chat 页提供文末来源列表、文内可点击角标及分片原文查看器。completed / partial / stop 终态均须尽力落库 citations 以保证刷新回放。

## Requirements

### Requirement: Citation SHALL 为稳定的结构化对象

系统 SHALL 为每条可引用的知识库分片构造 **Citation**，至少含下列字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `citation_id` | string | 稳定标识，见「citation_id 算法」Requirement |
| `index` | integer | 1-based 序号，同一 assistant 回合内全局递增，上限 50 |
| `collection_name` | string | 知识库 Collection |
| `file_name` | string | 文档名 |
| `shard_id` | string \| null | 可 retrieve 的 Qdrant point id；无法解析时为 null |
| `chunk_index` | integer \| null | 文档内分片序号，来自检索 payload |
| `header_path` | string \| null | 章节路径 |
| `snippet` | string | 展示摘要，SHALL 截断至不超过 280 字符 |
| `score` | number \| null | 检索相关度 |

同一回合内相同 `(collection_name, file_name, shard_id)`（`shard_id` 均为 null 时以 `(collection_name, file_name, chunk_index)` 辅助去重）SHALL 仅登记一次，复用首次 `index`。

#### Scenario: 重复命中去重

- **WHEN** 同一回合内两次 `search_knowledge_base` 返回相同可解析 `shard_id` 的 hit
- **THEN** `CitationCollector` SHALL 仅保留一条 Citation，且 `index` 不变

#### Scenario: citation_id 稳定性

- **WHEN** 对相同 `collection_name`、`file_name`、`shard_id`（含均为 null）重复登记
- **THEN** 生成的 `citation_id` SHALL 与首次一致

### Requirement: citation_id SHALL 使用 UUID5 确定性算法

`citation_id` SHALL 按下列规则生成，前后端与测试 **SHALL** 使用同一算法：

```text
uuid5(NAMESPACE_URL, "noesis:citation:" + collection_name + "\0" + file_name + "\0" + (shard_id or ""))
```

#### Scenario: 样例输入输出锁定

- **WHEN** `collection_name=requirement_docs`、`file_name=用户登录_需求规格.md`、`shard_id=550e8400-e29b-41d4-a716-446655440000`
- **THEN** 单测 SHALL 断言 `citation_id` 等于该输入下的固定 UUID5 字符串（golden 写入测试文件）

### Requirement: shard_id SHALL 为可 retrieve 的 Qdrant point id 或为空

登记与工具输出中的 `shard_id` SHALL 按下列优先级解析，**SHALL NOT** 使用裸 `content_hash` 字符串冒充 point id：

1. `Document.metadata["point_id"]`（检索命中时由向量库写入）；
2. `VectorStorage.hash_to_uuid(metadata["content_hash"])`（SHALL 调用 `kb/retrieval/store.py` 内同一静态方法，禁止自实现）；
3. 均不可用 → `shard_id` 为 null，Citation 仍登记，原文查看走文档级 fallback。

`KbRetrievalService._doc_to_hit` SHALL 将上述结果写入 `KbSearchHit.id` 供工具层透出。

#### Scenario: 向量命中含 point_id

- **WHEN** 检索结果 metadata 含 `point_id`
- **THEN** hit 的 `shard_id` SHALL 等于该 `point_id`
- **AND** `GET .../shards/{shard_id}` SHALL 可返回分片详情

#### Scenario: 无 point_id 时有 content_hash

- **WHEN** metadata 无 `point_id` 但含 `content_hash`
- **THEN** `shard_id` SHALL 为 `VectorStorage.hash_to_uuid(content_hash)` 的字符串形式

#### Scenario: hybrid 检索集成可 retrieve

- **WHEN** 对真实 Collection 执行 `search_mode=hybrid` 的 `search_knowledge_base` 且命中非空
- **THEN** 至少一条 hit 的 `shard_id` SHALL 使 `GET .../shards/{shard_id}` 返回 200，或 SHALL 为 null 且抽屉 fallback 用例通过

#### Scenario: shard_id 为空时抽屉 fallback

- **WHEN** Citation 的 `shard_id` 为 null
- **THEN** `CitationSourceDrawer` SHALL 调用 `GET .../documents/{file_name}/shards` 并按 `chunk_index` 或 `header_path` 定位

### Requirement: chunk_index SHALL 从检索 payload 透出

`KbRetrievalService._doc_to_hit` SHALL 从分片 metadata / payload 读取 `chunk_index`（有则写入 `KbSearchHit`）；`search_knowledge_base` 工具 JSON SHALL 透传该字段。缺失时 MAY 省略键或为 null。

#### Scenario: payload 含 chunk_index

- **WHEN** Qdrant payload 含 `chunk_index: 4`
- **THEN** 工具 hit JSON SHALL 含 `"chunk_index": 4`

### Requirement: CitationCollector SHALL 登记检索命中并解析 cited 子集

系统 SHALL 在 `backend/domain/chat/citations/` 提供 `CitationCollector`，在 COMMON_QA 每轮 assistant 流式回答生命周期内：

1. 接收 `search_knowledge_base` 的 `hits` 并登记（`index` 上限 50）；
2. 对 **merged_text**（见下）解析 `[n]` 角标得到 `cited_indices`；
3. 产出 `items`：候选中 `index ∈ cited_indices`，按 `index` 升序；
4. 若存在候选且 `cited_indices` 为空，SHALL 取 score Top-5，`citation_fallback=true`。

**merged_text** SHALL 仅由本轮 assistant 消息中 **`type=text` 且 `parent_task_call_id` 为空** 的 part 按顺序拼接；**SHALL NOT** 包含 `reasoning`、`tool` 或子 Agent 嵌套 text。

`CitationCollector.register_hits` **SHALL** 仅在 `kb_search_tool._format_hits`（主线程，并行 `ThreadPoolExecutor` 完成之后）调用；**SHALL NOT** 在 `_search_one_collection` worker 内调用。

**角标解析** SHALL：

- 先剔除 fenced code block（` ``` ` / `~~~`）内文本再匹配；
- 使用 `\[(\d{1,3})\](?!\()`，支持 index 1–999 的写法，但仅保留已在 registry 登记且 `≤ 50` 的 index；
- 排除 Markdown 链接 `[n](url)`。

#### Scenario: 正文含角标时 cited-only

- **WHEN** 登记了 index 1、2、3，text parts 合并正文仅含 `[1]` 与 `[3]`
- **THEN** 最终 `items` SHALL 仅含 index 1 与 3
- **AND** `citation_fallback` SHALL 为 false

#### Scenario: reasoning 中的 [1] 不参与解析

- **WHEN** reasoning part 含 `[1]` 但 text parts 不含
- **THEN** `cited_indices` SHALL 为空（触发 fallback 或无 cited 项）

#### Scenario: 代码块内 [1] 不参与解析

- **WHEN** text part 为 `` ```\n[1]\n``` `` 且块外无 `[1]`
- **THEN** `cited_indices` SHALL 为空

#### Scenario: 无角标时 fallback

- **WHEN** 登记了至少一条 Citation，merged_text 无任何有效 `[n]`
- **THEN** 最终 `items` SHALL 为 score Top-5（不足 5 条则全部）
- **AND** `citation_fallback` SHALL 为 true

### Requirement: partial 与 stop 终态 SHALL 持久化 citations part

当 `status` 为 `partial`（用户 `/stop` 或意外断连）且本轮 `CitationCollector` 已登记候选时，落库路径 **SHALL** 调用 `finalize(merged_text)` 并 `append_citations`（若 `items` 非空）。

**调用顺序（硬约束）**：`_flush_ctx_text_buffer` → `_finalize_citations` → `builder.to_dict()` →（仅 stop）`append_user_stop_notice_to_content` → persist。`citations` part SHALL 位于用户停止提示 text 之前。

- `stop_chat`、`_persist_disconnect_partial` 均须遵守上述顺序。

**SSE**：仅 **正常 `finish`**（bridge）**保证**发出 `citations-available`。**`/stop` 与意外断连首版不保证**流式过程中收到该帧；用户 **SHALL** 可通过刷新会话从 DB 读取 `citations` part。

#### Scenario: 用户停止后刷新仍有来源列表

- **WHEN** 用户发起 COMMON_QA，检索命中且正文含 `[1]`，随后调用 `/stop`
- **THEN** 落库 assistant 消息 `status=partial` 且 `content.parts` SHALL 含 `type=citations`
- **AND** 刷新会话后前端 SHALL 渲染来源列表

#### Scenario: 断连后 DB 仍含 citations

- **WHEN** 流意外断开前已完成检索与部分正文，且 collector 可 finalize 出 items
- **THEN** `_persist_disconnect_partial` 写入的消息 SHALL 含 `citations` part

#### Scenario: stop 时流式过程可无来源列表

- **WHEN** 用户 `/stop` 且 SSE 连接在 finalize 前结束
- **THEN** 客户端 **MAY** 未收到 `citations-available`
- **AND** 刷新后 **SHALL** 仍展示来源列表

### Requirement: citation_fallback SHALL 向用户明示语义

当 `citation_fallback=true` 时，UI SHALL 说明：未检测到文内 `[n]` 角标，文末列表为检索相关来源，**不代表**模型逐条引用了这些分片。

#### Scenario: fallback 文案展示

- **WHEN** `citations` part 含 `citation_fallback: true`
- **THEN** CitationList 上方 SHALL 展示上述含义的简短说明

### Requirement: 首版能力边界 SHALL 被文档与验收承认

本能力 **SHALL NOT** 承诺：审计级逐句归因、`get_knowledge_document` 自动产生 citation、非半角角标（如 `【1】`）、PDF 页码级高亮。LLM 长期不写 `[n]` 时体验上限为 fallback 列表 + 工具折叠内原始 hits——须在 `design.md` §难度评估 与验收指标中跟踪。

#### Scenario: 验收记录 fallback 占比

- **WHEN** 完成首版手工验收（≥3 个固定问题）
- **THEN** 团队 SHALL 记录 `citation_fallback` 出现次数占比并写入验收备注

### Requirement: chat 页 SHALL 渲染文末来源列表

（同前）当 `content.parts` 含 `type: "citations"` 或流式收到 `citations-available` 时，在正文之后渲染 **CitationList**；`citation_fallback=true` 时展示说明文案。

#### Scenario: 流式展示来源列表

- **WHEN** SSE 收到 `citations-available` 且 `citations` 非空
- **THEN** chat 页 SHALL 在当前 assistant 气泡正文下方展示来源列表

#### Scenario: 历史消息回放

- **WHEN** 加载含 `type: "citations"` part 的消息
- **THEN** SHALL 渲染来源列表，无需重放 SSE

### Requirement: 文内 [n] 角标 SHALL 可点击并打开原文

前端 SHALL 将 **text part** Markdown 中、**fenced code block 外**的独立 `[n]` 渲染为可点击上标；点击打开 **CitationSourceDrawer**，行为见 shard_id Requirement。

Markdown 渲染层 **SHALL** 跳过代码块内的 `[n]`，与后端解析一致。

#### Scenario: 点击角标打开分片

- **WHEN** 用户点击正文 `[2]` 且 citations 含 index=2、`shard_id` 有效
- **THEN** SHALL 打开抽屉并高亮 `snippet`

#### Scenario: 点击文末来源卡片

- **WHEN** 用户点击 CitationList 中 index=1 的卡片
- **THEN** 行为 SHALL 与点击正文 `[1]` 一致

### Requirement: 未知 citation part 与 SSE 类型 SHALL 向后兼容

旧客户端：忽略 `citations-available`；跳过未知 `citations` part 不报错。

#### Scenario: 旧客户端忽略 citations 帧

- **WHEN** `useSSEStream` 未处理 `citations-available`
- **THEN** 流式对话 SHALL 仍可正常结束
