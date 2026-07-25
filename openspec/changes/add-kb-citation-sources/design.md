## Context

COMMON_QA 当前由 harness 的 `search_knowledge_base` 返回 JSON hits，平台 Bridge 将工具结果写入 assistant `tool` part，最终由 PersistSink 落库。检索 hit 已有 `file_name`、`header_path` 和内容，但 `KbSearchHit.id` 混用了 Qdrant point id、`content_hash` 与其它 id；前端来源详情 API 又直接接受 collection/shard，无法证明该 shard 确实属于当前 assistant 回答。

旧设计在 harness 工具中通过 contextvar 注册 Citation，并在 finish 时解析 `[n]`。这有五个结构问题：harness 反向感知平台消息模型；数字序号跨多次工具调用不稳定；stop/disconnect 需要复制 finalize；Top-K fallback 混淆“引用”和“检索”；公开 shard id 泄漏存储实现且授权上下文不足。

## Goals / Non-Goals

**Goals:**

- 在不破坏 harness 独立性的前提下，让知识库工具输出可机器读取的 provenance。
- 来源在 tool end 即进入 assistant builder，使 completed、partial、disconnect、HITL resume 自然复用同一快照。
- 稳定表达“模型明确引用”和“工具仅检索”，不夸大归因置信度。
- 来源详情访问以 assistant message 为授权根，刷新后可回放。
- 支持同一轮多次/并行检索，不依赖可变数字编号。

**Non-Goals:**

- 不承诺逐句审计级事实归因、PDF bbox 或跨文档段落对齐。
- 不为旧消息反向生成来源。
- 首版仅覆盖 COMMON_QA 的 `search_knowledge_base`。
- 不把 Qdrant point id、collection 内部名称设计为永久公开标识。

## Decisions

### 1. Harness 输出 provenance envelope，不持有 CitationCollector

`search_knowledge_base` 每个 hit 增加：

```json
{
  "source_ref": "kb_7f3a9c2d1e",
  "locator": {
    "collection_name": "requirement_docs",
    "document_key": "<file_hash-or-stable-document-key>",
    "chunk_index": 4,
    "point_id": "<optional-internal-qdrant-id>"
  },
  "title": "登录需求.md",
  "header_path": "登录 > 验证码",
  "snippet": "..."
}
```

`source_ref` 由规范化 locator 的 SHA-256 截断生成，只是模型可引用的回合内稳定 token，不是授权凭据。harness 不 import `noesis_server`，也不创建平台 message part。

选择该方案而不是 contextvar collector，是因为工具返回本身就是 Agent 与宿主之间的稳定边界；平台可以在 `on_tool_end` 解析已知 schema，而评测也能原样消费。

### 2. 平台在 tool end 登记 EvidenceSource

`LangGraphSseBridge` 仅对已知工具名和 schema version 解析 provenance，并调用 `AssistantMessageBuilder.upsert_sources()`。Source part 按 `source_ref` 去重，保留：

- `source_id`：平台生成的 message-scoped opaque id；
- `source_ref`：供模型正文引用；
- `status`: `retrieved | cited`；
- `title`、`header_path`、截断 snippet snapshot；
- 私有 locator：collection、document key、chunk index、可选 point id；
- `tool_call_ids`：审计检索来源，不暴露给普通 UI。

登记发生在 tool end，因此 stop、disconnect 与 HITL pending 时 builder 已含来源，无需三套 collector finalize。HITL resume 从落库 content 恢复 source part 后继续去重。

### 3. 模型引用稳定 token，前端映射数字角标

提示词要求模型使用 `[[source:kb_xxx]]`。后端只解析顶层 assistant text part，忽略 reasoning、tool、子 Agent text、fenced code 和 inline code。命中已登记 `source_ref` 才把对应 source 标为 `cited`；未知 token 保持普通文本并记录观测，不生成来源。

前端按正文首次出现顺序把有效 token 渲染成 `[1]`、`[2]`，来源列表使用同一映射。数字只属于展示，不进入模型协议和持久化身份。

没有有效 token 时，来源保持 `retrieved`。UI MAY 在折叠区域显示“本轮检索来源”，但 SHALL NOT 使用“引用”文案。取消旧方案 `citation_fallback=true`。

### 4. 来源事件分为 available 与 finalized

平台通过统一 RunEvent/Delivery 发出：

- `sources-available`：紧随对应 `tool-output-available`，包含新增/更新的 retrieved sources；
- `sources-finalized`：正文结束后、`finish` 前发送 cited source ids 与最终展示顺序。

断连不发送后续 SSE，但 PersistSink 仍使用 builder 快照；stop API 不需要向另一条 SSE 注入事件。客户端刷新后从消息 content 恢复相同结果。

### 5. 来源详情以 assistant message 为授权根

新增 `GET /api/chat/messages/{message_id}/sources/{source_id}`。Service SHALL：

1. 加载 assistant message 与所属 session；
2. 校验当前用户拥有 session/message；
3. 从该消息持久化 source part 查 locator，拒绝客户端自带 collection/shard；
4. 重新校验 collection 仍在用户可访问范围；
5. 优先按内部 point id 读取，失败时按 document key + chunk index 回退；
6. 返回当前片段和 snapshot 状态，找不到时返回 404/410 语义而非跨库搜索。

公开 `source_id` 不可反解 Qdrant id。即使用户猜到另一个 source id，也无法脱离 message ownership 读取。

### 6. 文档身份与重建策略

point id 只用于当前存储快速定位。长期 locator 以 `document_key`（优先 file hash/文档稳定 id）+ `chunk_index` 为主，并保留 snippet snapshot。重新分块后无法精确定位时，UI 仍可展示 snapshot，但 SHALL 标记“原文已更新或不可定位”，不能静默打开相似但不同的片段。

## Risks / Trade-offs

- **[Risk] 模型仍可能不输出 source token** → 明确显示为“检索来源”，记录 cited/retrieved 比率，不伪造引用。
- **[Risk] 工具 JSON 增大上下文** → snippet 和 provenance 字段设硬上限，平台 source part 去重。
- **[Risk] 文档重建后 locator 失效** → point id 快路径 + document key/chunk fallback + snapshot 降级状态。
- **[Risk] Bridge 解析工具 schema 形成特例** → 使用 versioned provenance envelope，解析器独立于 KB service，可供未来工具复用。
- **[Trade-off] 两个 SSE 事件比 finish-only 复杂** → 换取 stop/disconnect 无专用 finalize、来源更早可见和状态语义准确。

## Migration Plan

1. 先扩展 harness KB hit schema 与边界测试，确保不 import 平台模块。
2. 增加平台 SourcePart、provenance parser、builder 恢复/去重和 RunEvent 映射。
3. 增加 assistant-scoped source detail API 与授权测试。
4. 前端先支持未知/新 source part 的 tolerant 解析，再启用 SSE 和 UI。
5. 更新 COMMON_QA prompt，最后开启 source token 解析与 cited 状态。
6. 全量后端测试、前端 lint/build、hybrid 集成测试和 stop/断连/HITL 回放测试通过后发布。

回滚时可停止 prompt token 与来源事件发射；已落库 source part 由 tolerant 客户端忽略，不影响正文和 tool parts。

## Open Questions

- `document_key` 采用现有 `file_hash`，还是补正式 document id；实现前用迁移检查确认覆盖率。
- `sources-available` 是否向普通用户默认折叠 retrieved sources，交由 UI 验收决定，不影响协议。
