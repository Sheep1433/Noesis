## Context

COMMON_QA 通过 `backend/packages/harness/noesis/tools/kb_search_tool.py` 返回检索 hits，`LangGraphSseBridge` 将工具过程转换为 `/api/chat` SSE，`AssistantMessageBuilder` 累积同一条 assistant 消息并由 PersistSink 持久化。当前消息只有 `text/reasoning/tool` part，知识库 hit 的身份又混用了 point id、hash 和 chunk index。

研究基线见 `docs/research/kb-citation-source-tracing.md`。OpenAI Responses 将两类数据明确拆开：答案正文的 `output_text.annotations` 表示 cited，`file_search_call.results` 表示 retrieved；流式中 `response.output_text.delta` 与 `response.output_text.annotation.added` 独立投递。Noesis 采用同一产品和协议形态，但检索、证据身份、授权回源仍由自建 KB 实现。

## Goals / Non-Goals

**Goals:**

- 引用是 text part 的 typed annotation，不是答案字符串中的 marker。
- retrieved manifest 与 cited annotations 分层，能独立持久化、回放和观察。
- 同一轮多次或并行检索后，模型只能绑定本轮实际 evidence；平台执行确定性结构校验。
- completed、partial、disconnect 与 HITL resume 共用同一消息快照。
- 点击引用以 assistant message 为授权根，并能定位到文档版本和 segment。

**Non-Goals:**

- 不迁就 Noesis 旧 CitationCollector、`source_ref`、SourcesPart、finish-only finalize 或 marker parser。
- 不要求底层字段与 OpenAI API 字面完全相同；Noesis 可增加自建 KB 必需的 citation id、offset 和 locator。
- 首版不做额外 LLM repair pass；不支持结构化绑定时宁可无引用。
- 不把所有 retrieval results 默认包装成答案依据。

## Decisions

### 1. 消息采用 text annotations + 独立 retrieval part

assistant 终态消息示例：

```json
{
  "parts": [
    {
      "id": "text_1",
      "type": "text",
      "content": "验证码有效期为 5 分钟。",
      "annotations": [
        {
          "type": "kb_citation",
          "citation_id": "cit_01H...",
          "start_index": 0,
          "end_index": 12,
          "document_id": "doc_123",
          "document_version_id": "docv_456",
          "segment_id": "seg_789",
          "title": "登录需求.md",
          "excerpt": "验证码发送后 5 分钟内有效。",
          "verification": "structural"
        }
      ]
    },
    {
      "id": "retrieval_1",
      "type": "retrieval",
      "tool_call_id": "call_abc",
      "query": "验证码有效期",
      "results": [
        {
          "evidence_id": "ev_a7f2",
          "document_id": "doc_123",
          "document_version_id": "docv_456",
          "segment_id": "seg_789",
          "title": "登录需求.md",
          "excerpt": "验证码发送后 5 分钟内有效。",
          "locator": {"type": "page", "page_start": 3, "page_end": 3},
          "score": 0.86
        }
      ]
    }
  ]
}
```

`annotations` 直接挂在被引用的顶层 text part 上。`start_index`/`end_index` 使用该 part `content` 的 Unicode code point、左闭右开区间；范围必须落在正文内。多个 annotation 可以覆盖同一范围，同一 citation 也可支持一个回答片段由多个 evidence 支撑。

`retrieval.results` 保留本轮工具看到的候选证据，供调试与折叠展示。它不是 citation 列表，不能从 score 或 Top-K 自动推导 `annotations`。

### 2. 模型输出结构化 answer binding，平台投影为 annotation

`search_knowledge_base` 每个最终 hit 只提供稳定 evidence envelope：`document_id + document_version_id + segment_id`、展示元数据、受限 excerpt、typed locator 与内部检索信息。它 SHALL NOT 自行从 `S1` 开始分配 run-local ID。

Harness/runtime 的 run 级 retrieval manifest 是 `evidence_id` 的唯一分配者。登记 hit 时以 `document_id + document_version_id + segment_id` 的 canonical serialization 为去重键，并使用 run-scoped salt 对该 key 做确定性派生，得到不暴露长期身份的短 opaque ID（例如 `ev_a7f2`）；发生短 ID 碰撞时确定性延长摘要。manifest 同时记录所有命中它的 `tool_call_id`。多次或并行工具调用命中同一 evidence SHALL 复用同一个 ID；不同 evidence SHALL 不得因各工具调用的局部序号发生冲突。分配与登记 SHALL 在并发下原子化；同一 run 的检查点恢复与重放必须复用原 salt，因此不依赖工具完成顺序。`evidence_id` 仅在一次 Agent run 的 manifest 内唯一，不持久承担文档身份或授权。

GeneralQAAgent 的生成契约为结构化 answer segments：

```json
{
  "segments": [
    {"text": "验证码有效期为 5 分钟。", "cited_evidence_ids": ["ev_a7f2"]}
  ]
}
```

Harness 负责 provider-specific structured output 适配，并向宿主提供统一 segment/binding 事件；平台按 `segments[]` 顺序原样、零分隔符拼接 `text`，不得隐式插入换行、空格或其它字符。段落分隔必须由 provider 放入某个 segment 的 `text`。offset 包含 segment 自带的全部字符，annotation 只覆盖该 segment 的非空原始范围，不覆盖相邻 segment。平台据此计算确定性 Unicode code point offset，并将有效 binding 投影为 `kb_citation` annotation。结构化对象是运行期生成协议，不作为用户可见正文或最终消息的第二份文本长期保存。

平台 SHALL 拒绝不存在于本轮 retrieval manifest 的 evidence id、空范围和越界范围，并记录观测。未知 binding 不产生 annotation，也不回退为 marker、文件名猜测或 Top-K 引用。若 provider 无法满足 typed schema，本轮 MAY 退化为纯文本回答 + retrieval part。

### 3. 稳定身份使用 document/version/segment

KB 在摄取时提供：

- `document_id`：业务上的文档身份；
- `document_version_id`：一次可回放的摄取快照；
- `segment_id`：该版本下稳定的证据单元；
- `locator`：可选 typed locator，首版仅允许以下结构之一：`{"type":"page","page_start":1,"page_end":1}`、`{"type":"char","char_start":0,"char_end":10}`、`{"type":"bbox","page":1,"x":0,"y":0,"width":1,"height":1}`、`{"type":"header","path":["章节"]}`；page 为 1-based，char 为原始 segment Unicode code point 左闭右开范围，bbox 使用页面归一化坐标；
- 内部 point id：仅作为当前向量存储快速读取提示，不进入公开契约。

hash 可用于去重和完整性检查，chunk index 可用于诊断，但两者都不作为长期 citation identity。旧版本仍存在时必须打开旧版本；旧版本被清理时返回 stale/missing，不以相似片段静默替代。

当前 Qdrant payload 的迁移映射确定为：`document_id = hash(collection_name, normalized file_name)`，因此同一 collection 内同名文档重新摄取仍保持业务身份；`document_version_id = hash(document_id, file_hash)`，其中 `file_hash` 是上传原文件 SHA-256；`segment_id = hash(document_version_id, chunk_index, content_hash)`，在一次不可变摄取版本内稳定。新入库同时把三层 ID 与 typed locator 写入 payload 顶层和 metadata 子对象，保证 vector、BM25、hybrid 与 rerank 使用同一 identity。

历史 payload 若缺少 `file_hash` 或任一三层 ID，检索层 SHALL 标记 `identity_status=legacy_unversioned`，可以作为 retrieved-only 结果返回，但 SHALL NOT 进入 citation binding。迁移方式是重新摄取原文件；平台不得用文件名、当前 chunk index 或 content hash 临时伪造历史版本身份。

### 4. 流式事件对齐 OpenAI annotation patch

文本继续使用现有 `text-start` / `text-delta` / `text-end`。新增：

- `retrieval-results-available`：对应工具结果登记完成后发送 retrieval part 或增量结果；
- `text-annotation-added`：某个 answer segment 完成结构校验后发送完整 annotation，包含 `text_part_id`；

推荐顺序为 tool output → retrieval results available → text delta/annotation added 交错 → text end → finish。annotation 可以晚于对应文本，不得早于其引用范围已写入 builder。这里 `text-end` 只表示正文字符交付结束，不封闭 text part 的 annotation 集合。

- provider 支持 segment streaming 时，完成 segment 的 annotation SHALL 最迟在对应 `text-end` 前登记；
- provider 只能终态返回完整 structured object 时，平台 MAY 先交付完整正文并发送 `text-end`，随后登记 annotation，但 SHALL 在 `finish` 与终态持久化之前完成；
- 异常或断连时，已验证且范围完整的 annotation 可保留，未闭合 segment 不生成 annotation。

PersistSink 读取 builder 终态快照，不依赖客户端是否收到事件。HITL pending/resume 继续同一 `run_id`、`assistant_message_id`、retrieval manifest 与 evidence namespace；resume 不重新分配已有 `evidence_id`。恢复和 resume 时 citation 按 `citation_id` 去重，retrieval evidence 按稳定 identity 去重，`tool_call_id` 作为命中关联集合合并。

### 5. 只做确定性的结构校验

首版每条 annotation 必须同时满足：

1. evidence id 来自本轮已登记 retrieval manifest；
2. document/version/segment 与 manifest 完全一致；
3. offset 位于对应顶层 text part，且覆盖非空文本；
4. locator schema 可解析；
5. citation resolve 时当前用户仍具备访问权限。

通过后标记 `verification=structural`。该值不声称 evidence 在语义上足以支持结论。后续若引入 verifier，只能增加 `semantic_verified/weak/rejected` 等结果，不得改写原始 evidence identity。

### 6. 引用详情以 assistant message 为授权根

新增 `GET /api/chat/messages/{message_id}/citations/{citation_id}`。`ChatService`（或独立 citation service）必须：

1. 加载 assistant message 与 session，验证当前用户 ownership；
2. 仅从消息 text annotation 取得 document/version/segment，不接受客户端 locator；
3. 重新校验 collection、document 与 version 的当前读取权限；
4. 重新实读精确 version/segment，返回最小必要 excerpt、title、typed locator 和受控 viewer/download 信息；
5. 精确版本或 segment 不存在时返回 missing/stale 语义，不做语义搜索替换。

annotation/retrieval part 中的 excerpt 是生成时落库快照，只用于历史消息的最小展示与审计，不等价于一次成功 resolve。resolve 主路径 SHALL 返回重新鉴权后实读的精确版本内容；权限撤销时返回 forbidden，版本或 segment 缺失时返回 stale/missing。服务端不得把快照伪装成实读成功。客户端是否仍可展示已随消息落库的快照，由既定脱敏策略决定，并必须与 resolve 状态区分。

### 8. Retrieval manifest 使用容量预算

首版继续把 retrieval part 保存在同一 assistant message snapshot，不先拆独立表，但所有入口 SHALL 执行统一容量预算。预算项至少包含：每次工具调用 results 数、单 run 去重 evidence 数、单 excerpt Unicode 字符数与 UTF-8 bytes、单 locator JSON bytes、单 assistant `content` JSON bytes。具体默认值由容量 spike 基于当前数据库字段、真实 chunk 与多次并行检索样本确定，随后固化为配置和 golden tests。

达到单项上限时平台 SHALL 确定性保留排序靠前且 identity 去重后的结果，截断 excerpt 并记录 `truncated` 元数据与指标；不得生成半个 locator、删除已经被正文绑定的 evidence，或让消息体继续无界增长。若 cited evidence 超出预算，平台 SHALL 优先保留 cited evidence，并从 retrieved-only 结果中腾出预算。

### 7. 前端只消费 typed annotation

前端按 `start_index/end_index` 在 Markdown 渲染前建立 annotation range，再把角标作为独立 UI 节点插入。正文不含任何内部 marker，复制纯文本时不泄漏 citation token。点击角标调用 message-scoped resolve API。

默认来源区仅聚合 cited annotations；retrieved-only results 放入折叠的“本轮检索结果”。调试字段如 score、tool_call_id 不进入普通用户主视图。

## Errors and Degradation

| 情况 | 行为 |
|------|------|
| provider 不支持或破坏结构化输出 | 保留纯文本和 retrieval results，不生成 cited annotation |
| binding 引用未知 evidence id | 丢弃该 binding、记录指标，正文照常交付 |
| offset 计算或恢复不一致 | 不展示该 annotation；不得猜测新范围 |
| 文档版本删除或权限撤销 | resolve 返回 404/410/403 对应语义，已落库 excerpt 仅按既定脱敏策略展示 |
| SSE 断连 | 后台继续构建与落库；刷新读取权威消息快照 |

## Migration and Rollback

1. 先补 KB document/version/segment identity 与 tool evidence envelope。
2. 实现 provider structured binding spike；验证 DeepSeek/Qwen 当前端点能稳定输出 segments 后再接平台。spike 是启用 COMMON_QA citation 的发布门禁；不通过时只启用纯文本 + retrieved-only 降级。
3. 扩展 message builder、持久化与 SSE typed events。
4. 实现 resolve API 与权限测试。
5. 前端支持 annotations/retrieval tolerant parsing 后启用 COMMON_QA。

旧 change 尚未实现，因此不迁移 `source_ref` 或 SourcesPart 数据。回滚只需停用 structured binding 与新事件；旧客户端仍显示 text/tool part，新客户端忽略不存在的 annotations。

## Risks / Trade-offs

- **结构化输出可能影响逐 token 流式体验**：优先按 segment 增量交付；provider 只能终态返回时允许 citation 晚到，但不退回 marker。
- **offset 与 Markdown 变换可能错位**：协议 offset 永远基于原始 text part；渲染层维护原文到 AST 的映射，不对渲染后 HTML 重新计数。
- **retrieval manifest 增大消息体积**：通过容量 spike 固化可配置硬上限，优先保留 cited evidence；完整 tool output 不重复复制到普通 UI。真实样本若仍逼近数据库安全阈值，再改为 message-scoped 独立表，不在首版预先拆分。
- **仅结构校验不等于事实验证**：UI 不使用“已证实”文案，先以观测数据决定是否增加语义 verifier。

## Open Questions

- 当前 DeepSeek/Qwen provider 对 JSON schema 与 segment streaming 的真实稳定性需要在任务 2.1 用固定样本验证；结果只决定“逐段流式或终态 annotation”，不改变 typed annotation 协议。
- 文档版本保留策略由 KB 生命周期规格另行确定；本变更只要求旧版本缺失时显式 stale/missing。
