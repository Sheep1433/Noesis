# 知识库引用溯源研究报告

> 状态：Research / Proposed
> 调研日期：2026-07-28
> 关联 OpenSpec：[`add-kb-citation-sources`](../../openspec/changes/add-kb-citation-sources/)

## 1. 结论

Noesis 应采用 OpenAI Responses 风格的引用模型：**检索到的证据（retrieved）与回答实际引用的证据（cited）分层，引用作为 text part 的 typed annotation 存在，流式正文与 annotation 使用独立事件交付。**

OpenAI 的产品和协议形态值得采用，但它的托管 File Search 不能直接承载 Noesis 自建的 Qdrant、DeepDoc、hybrid retrieval 和 rerank。因此 Noesis 只自建无法由 OpenAI 托管能力替代的部分：稳定 evidence identity、run 级 manifest、structured answer binding、消息持久化、权限回源和确定性校验。

首版不使用正文 marker，不从 Top-K 猜测引用，也不额外调用 LLM 修复引用。目标 provider 无法稳定输出 typed binding 时，该轮退化为纯文本回答与 retrieved-only results；回答仍可交付，但 UI 不宣称存在引用。

具体 SHALL、Scenario 和实现任务以 OpenSpec 为准：

- [设计决策](../../openspec/changes/add-kb-citation-sources/design.md)
- [引用能力规格](../../openspec/changes/add-kb-citation-sources/specs/chat-kb-sources/spec.md)
- [实现任务](../../openspec/changes/add-kb-citation-sources/tasks.md)

## 2. 调研目标与范围

本报告回答四个问题：

1. 引用应当属于答案字符串、消息对象，还是检索工具元数据？
2. 如何区分“模型看过的证据”和“答案实际引用的证据”？
3. Noesis 如何在多次/并行检索、流式输出、断线恢复和 HITL resume 下维持稳定引用？
4. 哪些能力可以借鉴 OpenAI，哪些必须由自建 KB 平台实现？

范围只覆盖 `COMMON_QA` 的知识库检索。Web Search、会话附件、SuperAgent 和跨消息全局 citation identity 不在首版范围内。

## 3. Noesis 现状

### 3.1 当前调用链

当前链路是：

```text
GeneralQAAgent
  → search_knowledge_base
  → hybrid / BM25 / vector / rerank hits
  → LangGraphSseBridge
  → text/tool RunEvent
  → AssistantMessageBuilder
  → PersistSink
  → 一条 assistant message content.parts
```

主要入口：

- `backend/packages/harness/noesis/tools/kb_search_tool.py`：执行知识库检索并把 hits 返回给 Agent。
- `backend/noesis_server/domain/chat/streaming/langgraph_sse.py`：把 LangGraph 消息和工具过程投影为流式事件。
- `backend/noesis_server/domain/chat/message_builder.py`：累积 `text/reasoning/tool` parts。
- `backend/noesis_server/domain/chat/delivery/persist_sink.py`：按检查点和终态保存同一条 assistant 消息。
- `frontend/src/views/chat/messageParts.ts`：解析并恢复前端消息 parts。

### 3.2 已确认的缺口

截至调研日期，当前实现没有正式的 `TextPart.annotations`、`RetrievalPart` 和 message-scoped citation resolve API。检索 hit 的身份还不足以同时表达业务文档、摄取版本和稳定 segment；工具结果能说明“检索到了什么”，但不能说明“答案哪一段实际使用了什么”。

Durable Agent Run 已建立一个重要基础：浏览器不是消息状态权威，断线后 producer 和 PersistSink 继续工作，HITL resume 使用同一个 run 和 assistant message。引用设计必须进入同一个 builder snapshot，不能只在前端内存中拼接。

## 4. 外部方案

### 4.1 OpenAI Responses API

已确认事实：

- Web Search 把正文放在 output text 中，把 `url_citation` 放在该 text item 的 `annotations` 中。
- Web Search 的完整 `sources` 集合可能大于 inline citations 集合，说明 retrieved 与 cited 是不同状态。
- 流式协议将 `response.output_text.delta` 与 `response.output_text.annotation.added` 分开交付。
- File Search 的检索调用结果与回答中的 `file_citation` annotation 分离。
- File Search 是 OpenAI 托管工具；自定义 function tool 不能自动获得相同的 `file_citation` 能力。

设计分析：

OpenAI 最值得借鉴的不是字段名称，而是状态边界：正文是正文，annotation 是正文上的结构化关系，工具结果是独立的 retrieved manifest。客户端丢失某个 annotation patch 时，终态 response object 仍是权威来源。

对 Noesis 的启示：

- 采用 text annotations + retrieval part。
- 使用独立 annotation RunEvent，不在正文中嵌入内部 token。
- 保持 retrieved 全集与 cited 子集可独立观察和持久化。
- 不接入 OpenAI 托管 File Search，保留自建检索链路。

### 4.2 Anthropic Citations 与 Search Results

已确认事实：

- Anthropic 可以把自定义工具返回的结果包装为结构化 `search_result` content block。
- 启用 citations 后，Claude 在 text block 上返回结构化 citations。
- 流式通过 citation delta 独立增加 citation。
- Citation 可以使用 char、page 或 content block 等定位方式。

设计分析：

Anthropic 进一步证明了“自定义检索结果 → 结构化 source → text citation”的路线成立。但这项 citation 生成能力属于 Claude provider，不能作为 DeepSeek/Qwen 的通用运行时能力。

对 Noesis 的启示：

- KB 工具应输出结构化 evidence envelope，而不是为模型拼接自由文本来源说明。
- locator 应是带类型的结构，不应是任意 JSON。
- provider 原生 citation 可以作为能力上限，但 Noesis 的协议不能绑定单一模型供应商。

### 4.3 RAGFlow

已确认事实：

- RAGFlow 为检索 chunk 分配 ID，并通过 prompt 要求模型在答案字符串中输出 `[ID:n]` 一类 marker。
- 服务端需要额外解析、修复和映射这些 marker。
- 公开 issue 中出现过 marker 泄漏和历史映射混乱问题。

设计分析：

Marker 路线容易启动，但把协议状态混入自然语言。Markdown、代码块、模型改写、流式截断和复制正文都会让解析变复杂。它也迫使服务端长期维护 parser 和 repair 逻辑。

对 Noesis 的启示：

- 可借鉴其引用政策和 chunk 定位信息。
- 不采用 marker parser，也不把 hash 取模或数组位置当长期 identity。

### 4.4 Dify

已确认事实：

- Dify 在工具链路累积 retrieval metadata，包含 dataset、document、segment、score、content 和 page 等信息。
- 来源以 retriever resources 形式附加到回答，架构上接近 Noesis 的 tool metadata。
- 公开链路更偏 answer-level retrieved resources，没有 OpenAI 式 span-level cited/retrieved 分层。

设计分析：

Dify 证明了工具层累积来源元数据适合自建 RAG，但仅有 retrieval metadata 仍无法回答“哪段正文由哪条 evidence 支撑”。

对 Noesis 的启示：

- 借鉴工具层 evidence envelope 和 document/segment 分层。
- 不用结果位置承担持久身份，不把所有 retrieved result 默认显示为引用。

## 5. 横向比较

| 方案 | cited 与 retrieved | 与正文耦合 | 自定义检索 | 流式引用 | Noesis 取舍 |
|---|---|---|---|---|---|
| OpenAI Responses | 明确分层 | typed annotation | 原生 citation 依赖托管工具 | annotation event | 采用对象与事件形态 |
| Anthropic | source/result 与 text citation 分层 | typed block | 支持 search result，但绑定 Claude | citation delta | 借鉴 source/locator 结构 |
| RAGFlow | 通过 marker 映射 | 强耦合 | 支持 | 易受截断影响 | 不采用 marker |
| Dify | 主要是 retrieved resources | 与正文 span 弱关联 | 支持 | 多在消息元数据阶段 | 借鉴工具元数据累积 |

## 6. 推荐设计

### 6.1 消息模型

一条 assistant 消息继续只落库一行，但 `content.parts` 增加两种信息：

- 顶层 `text` part 的 `annotations[]`：回答实际引用的证据关系。
- 独立 `retrieval` part：工具在该轮返回并登记的 evidence manifest。

`cited` 是 typed answer binding 经结构校验后的 `retrieved` 子集，不能由 score、Top-K 或文件名推导。

### 6.2 Evidence identity

长期身份使用：

```text
document_id + document_version_id + segment_id
```

KB 工具只返回稳定 identity，不分配 `S1/S2`。Harness/runtime 的 run 级 manifest 使用 run salt 与 canonical identity 确定性派生 opaque `evidence_id`，原子处理并行登记、重复命中和短 ID 碰撞。同一 run 的检查点恢复和 HITL resume 复用 manifest 与 salt。

数组位置、chunk index、内容 hash 和 Qdrant point id 可以用于排序、诊断或快速读取，但不进入长期公开 citation identity。

### 6.3 Structured answer binding

GeneralQAAgent 的运行期输出采用 typed segments：

```json
{
  "segments": [
    {
      "text": "验证码有效期为 5 分钟。",
      "cited_evidence_ids": ["ev_a7f2"]
    }
  ]
}
```

平台按 segments 顺序零分隔符原样拼接，不隐式插入空格或换行。所有段落分隔必须由 provider 写入 segment text。这样流式正文与终态正文使用同一字符序列，Unicode code point offset 可确定重放。

Provider structured output 需要先通过固定样本 spike。未通过时只交付纯文本与 retrieved-only results，不启用 citation，不回退到 marker。

### 6.4 流式时序

统一约束是：annotation 不得早于引用范围进入 builder，并且必须在 `finish` 和终态持久化前完成登记。

```text
segment streaming
  retrieval → text delta → annotation patch → text-end → finish

terminal structured output
  retrieval → complete text → text-end → annotation patch → finish
```

`text-end` 只表示正文字符交付结束，不封闭 text part 的 annotation 集合。终态 provider 因此可以在完整 JSON 可用后计算 offset，同时不破坏 PersistSink 权威快照。

### 6.5 结构校验

首版只声明 `verification=structural`。平台确定性验证：

- evidence id 属于本轮 manifest；
- document/version/segment 与 manifest 一致；
- annotation range 非空且位于对应 text part；
- typed locator 可解析；
- resolve 时用户仍具备读取权限。

这不表示 evidence 在语义上足以支持结论。是否增加 semantic verifier，应由 structured binding 成功率、未知 evidence、降级率和用户反馈决定。

### 6.6 Locator 与 resolve

Locator 使用带 `type` discriminator 的 page、char、bbox 或 header 结构，并明确页码、字符单位和 bbox 坐标系。

点击引用调用 message-scoped API。服务端以 assistant message 为授权根，重新校验 session/message ownership 和 KB 权限，再实读精确 document version 与 segment。annotation 中的 excerpt 是生成时快照，只用于历史消息的最小展示；权限撤销或证据缺失时必须返回 forbidden、stale 或 missing，不能把快照伪装成实读成功，也不能用相似 chunk 替换。

### 6.7 容量预算

Retrieval part 首版仍放在消息快照内，不预先拆表，但必须为以下项目设置可配置硬上限：

- 每次工具调用的 results 数；
- 单 run 去重 evidence 数；
- excerpt 字符数和 UTF-8 bytes；
- locator JSON bytes；
- assistant content JSON 总 bytes。

超限时优先保留 cited evidence，再确定性截断 retrieved-only evidence，并记录 `truncated` 元数据和指标。如果真实样本仍逼近数据库安全阈值，再评估 message-scoped 独立表。

## 7. 不采用的方案

### 7.1 正文 marker

不采用 `[[source:...]]`、`[ID:n]` 或文件名角标作为模型协议。原因是它污染自然语言、依赖 parser、容易被 Markdown 和流式截断破坏，并会泄漏到复制文本和历史上下文。

### 7.2 Top-K 自动引用

检索得分只说明候选相关性，不能证明回答实际使用了该证据。把 Top-K 自动包装成引用会让 retrieved 冒充 cited。

### 7.3 首版 LLM repair/verifier

额外模型调用增加延迟、成本和新的不确定性。首版先完成可验证的结构关系；语义验证作为后续独立能力评估。

### 7.4 直接采用 OpenAI File Search

这会绕过 Noesis 已有的 Qdrant、DeepDoc、hybrid retrieval、rerank 和模型选择能力，不符合自建知识库边界。

## 8. 待验证问题

1. DeepSeek/Qwen 当前端点对 JSON schema、长答案、多 evidence binding 和 segment streaming 的稳定性。
2. Provider 只能终态返回 structured output 时，对首字延迟、完整答案延迟和前端体验的影响。
3. 当前 KB payload 能否可靠迁移出 document/version/segment 三层身份。
4. 真实多次/并行检索下的 manifest 大小，据此确定容量默认值。
5. 前端 Markdown AST 到原始 Unicode code point range 的映射策略，尤其是 emoji、链接、代码和组合字符。
6. 文档版本保留周期与历史 citation 的 stale/missing 比例。

这些问题对应 OpenSpec tasks 1.1、2.1、3.2、3.5、6.4 和 7.4，不在研究文档中预设答案。

## 9. 资料来源

### 官方文档

- [OpenAI File Search](https://developers.openai.com/api/docs/guides/tools-file-search)，读取日期 2026-07-28。
- [OpenAI Web Search](https://developers.openai.com/api/docs/guides/tools-web-search)，读取日期 2026-07-28。
- [Anthropic Citations](https://platform.claude.com/docs/en/build-with-claude/citations)，读取日期 2026-07-27。
- [Anthropic Search Results](https://platform.claude.com/docs/en/build-with-claude/search-results)，读取日期 2026-07-27。

### 开源实现

- RAGFlow：`infiniflow/ragflow`，commit `53e83dcadfef`，重点参考 `rag/prompts/generator.py`、`citation_prompt.md`、`citation_plus.md`。
- Dify：`langgenius/dify`，2026-07-27 HEAD，重点参考 `api/core/rag/entities/citation_metadata.py` 与 `dataset_retriever_tool.py`。
- Kotaemon：`Cinnamon/kotaemon`，commit `9ad3e4e49aa3`，作为 phrase matching verifier 的后续参考，不进入首版方案。

### Noesis

- [`add-kb-citation-sources` design](../../openspec/changes/add-kb-citation-sources/design.md)，Proposed，2026-07-28。
- [`chat-kb-sources` spec](../../openspec/changes/add-kb-citation-sources/specs/chat-kb-sources/spec.md)，Proposed，2026-07-28。
- [`knowledge-base` spec](../../openspec/changes/add-kb-citation-sources/specs/knowledge-base/spec.md)，Proposed，2026-07-28。
- [`platform-chat` spec](../../openspec/changes/add-kb-citation-sources/specs/platform-chat/spec.md)，Proposed，2026-07-28。

## 10. 状态说明

本文描述的是研究结论和 Proposed 方案，不代表当前代码已经具备引用溯源。实现完成并验收后，应更新 `docs/architecture/knowledge-base.md` 与 `docs/architecture/platform/chat-streaming.md`，而不是把本报告改写成 Current 架构。
> 状态（2026-08-01）：本文早期推荐的 typed annotation 方案已被后续源码验证否决。当前实现采用 Prompt Markdown citation；现行决策以 `openspec/changes/add-kb-citation-sources/` 和 `docs/architecture/platform/chat-streaming.md` 为准。本文保留为历史调研证据，不再作为实现建议。
