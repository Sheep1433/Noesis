## Context

当前 COMMON_QA 链路：

```
GeneralQAAgent
  → search_knowledge_base（返回 hits JSON，含 file_name / header_path）
  → tool-output-available（落入 content.parts type=tool）
  → LLM 生成 text-delta（正文可能手写来源名）
  → finish → 落库 text + tool parts
```

痛点与约束见 `proposal.md`。补充现网约束：

- **SSE 键名**：`LangGraphSseBridge` 与 `useSSEStream` 现网统一 **snake_case**（`message_id`、`part_id`、`text_delta`、`parent_task_call_id`），golden 测试同风格。
- **shard_id**：入库时 Qdrant point id 为 `hash_to_uuid(content_hash)`；向量检索会在 `metadata.point_id` 写入真实 point id，但 `_doc_to_hit` 若回退到裸 `content_hash` 则 `retrieve` 会 404。
- **partial 落库**：`stop_chat` / `_persist_disconnect_partial` 当前直接 `builder.to_dict()`，不经过 bridge finalize。

首版范围：**仅 `qa_type=COMMON_QA`** + `search_knowledge_base`。

## Goals / Non-Goals

**Goals:**

- 端到端 Citation：登记 → 正文 `[n]` → cited 子集 → SSE（snake_case）→ `CitationsPart` 落库 → UI。
- **completed / partial / stop** 三种终态均尽力写入 `citations` part（保证刷新后可回放）。
- 单测 + 可选 eval 监控 `[n]` 引用率。

**Non-Goals:**

- `get_knowledge_document` 自动 citation、PDF bbox、其它 qa_type UI、独立 REST、用户编辑来源。

## Decisions

### 1. Citation 数据模型与 citation_id

```python
@dataclass
class Citation:
    citation_id: str       # UUID5，见下
    index: int             # 1-based，回合内全局递增
    collection_name: str
    file_name: str
    shard_id: Optional[str]  # Qdrant point id；无法解析时为 None
    chunk_index: Optional[int]
    header_path: Optional[str]
    snippet: str           # ≤ 280 字符
    score: Optional[float]
```

**citation_id 算法（规格化）**：

```text
uuid5(NAMESPACE_URL, f"noesis:citation:{collection_name}\0{file_name}\0{shard_id_or_empty}")
```

- `shard_id` 为空时第三段用空字符串；同一三元组跨端稳定。
- 单测 golden 须锁定样例输入输出。

**回合内序号上限**：`index` 最大 **50**；超出不再分配新 citation（工具仍返回 hit 但无 `citation_index`），避免角标爆炸。

### 2. shard_id 来源与空值

**决策**：`shard_id` SHALL 仅为可 `retrieve` 的 Qdrant point id。

| 优先级 | 来源 |
|--------|------|
| 1 | `Document.metadata["point_id"]`（向量/混合检索命中时由 store 写入） |
| 2 | `hash_to_uuid(metadata["content_hash"])`（与入库 `add_vectors` 一致） |
| 3 | 无法解析 → `shard_id=None`，Citation 仍登记；抽屉走文档级 fallback |

**实现要求**：修改 `_doc_to_hit`，`KbSearchHit.id` 按上表解析；**禁止**将裸 `content_hash` 字符串作为 `shard_id` 透出。

`chunk_index`：**硬依赖** — `_doc_to_hit` 从 payload metadata 写入 `KbSearchHit` 新字段；`kb_search_tool` 透传至 hit JSON。

### 3. merged_text 与角标解析

**merged_text 范围**：

- **仅**合并 assistant 回合内所有 **`type=text`** part 的 `content`，按 parts 顺序拼接；
- **排除** `reasoning`、`tool`、子 Agent 嵌套 text（带 `parent_task_call_id` 的 text 仍计入主回答正文，与现网主气泡展示一致）；
- 流式路径可用 `AssistantMessageBuilder` 累积的 text parts 等价实现。

**角标解析**（`resolve.py`）：

1. 对 merged_text 先剔除 fenced code block（` ``` ` 与 `~~~` 包裹段），再解析；
2. 正则：`\[(\d{1,3})\](?!\()` — 支持 1–999，排除 Markdown 链接 `[n](url)`；
3. 解析结果 `cited_indices` 仅保留 `1 ≤ n ≤ 50` 且已在 registry 登记的 index；
4. reasoning 内容 **不参与**解析（因 merged_text 不含 reasoning）。

**cited 子集与 fallback**：与初版相同；fallback Top-5，`citation_fallback=true`。

### 4. SSE：`citations-available`（snake_case）

**时序**：在 **`finish` 之前**发出；若本轮曾发出 `text-end`，则在其后；**若无 `text-end`（无正文流）**，则在最后一个业务帧（如 `tool-output-available`）之后、`finish` 之前发出。

```json
{
  "type": "citations-available",
  "message_id": "msg-xxx",
  "part_id": "part-citations-xxx",
  "citations": [
    {
      "citation_id": "uuid",
      "index": 1,
      "collection_name": "requirement_docs",
      "file_name": "登录PRD.md",
      "shard_id": "abc-123",
      "chunk_index": 4,
      "header_path": "登录PRD.md > 验证码",
      "snippet": "验证码有效期为 5 分钟……",
      "score": 0.87
    }
  ],
  "citation_fallback": false
}
```

- SSE 与落库 **均 snake_case**；前端 `useSSEStream` 直接读 snake_case，与 `text-delta` 一致。
- `citations` 数组项字段与 `CitationsPart.items[]` 同形。

### 5. 持久化与终态路径

**共享函数** `_finalize_citations(builder, collector) -> None`：

1. `merged_text = builder.merged_text_content()`（仅 text parts）；
2. `items, fallback = collector.finalize(merged_text)`；
3. 若 `items` 非空 → `builder.append_citations(items, citation_fallback=fallback)`。

**调用点**（均须调用共享函数）：

| 路径 | SSE `citations-available` | 落库 `citations` part |
|------|---------------------------|------------------------|
| 正常 `finish` | 发出（连接仍开） | 是 |
| `stop_chat` | 若 SSE 仍连接则发出 | 是（`status=partial`） |
| 意外断连 `_persist_disconnect_partial` | 通常不再发（连接已断） | 是（`status=partial`） |

用户停止后刷新：**须**能从 DB 读到 `citations` part，即使未收到 SSE 引用帧。

### 6. CitationsPart

```json
{
  "type": "citations",
  "items": [ { "citation_id", "index", "collection_name", "file_name", "shard_id", ... } ],
  "citation_fallback": false
}
```

`shard_id` 可为 `null`。插入于 text parts 之后、终态 persist 前。

### 7. 提示词、前端、Langfuse

与初版一致；Markdown 角标仅处理 **text part** 渲染路径中的 `[n]`（代码块内由渲染层跳过，与后端解析一致）。

Langfuse：`finalize` 写入 cited 摘要 metadata。

### 8. 部署顺序

**前后端须同版本发布**：后端先落库 `citations` part 而前端未解析会导致刷新丢失列表。tasks 规定：前端 `5.x` 与后端 `3.x` **同一 PR / 同一发布**，或前端先合 tolerant 解析。

### 9. 验收与 eval

- **手工验收**：`backend/kb/seed/requirement_docs/` 入库至测试 Collection，提问「用户登录密码规则」类事实问题，检查 `[n]`、来源列表、抽屉。
- **自动化 eval**（非阻塞 CI）：`backend/tests/test_citation_e2e_prompt.py` 或 `evals/` 下 mock LLM 输出含 `[1]` 的样例，断言 collector + builder 产出 citations part。

## Risks / Trade-offs

- **[Risk] LLM 不写 `[n]`** → fallback Top-5 + `citation_fallback` UI。
- **[Risk] BM25 路径未写 point_id** → `hash_to_uuid(content_hash)` 回退；单测覆盖。
- **[Risk] 断连无 SSE 引用帧** → 依赖 DB partial 落库 citations；刷新可恢复。
- **[Trade-off] 代码块外仍可能误匹配列表项 `[1]`** → 剔除 fenced block 缓解；嵌套 inline code 首版不处理。

## Migration Plan

1. `domain/chat/citations/` + 单测（含 citation_id、resolve、shard_id）。
2. 修复 `_doc_to_hit` + `kb_search_tool` + collector contextvar。
3. `_finalize_citations` 接入 bridge、`stop_chat`、disconnect。
4. 前端 5.x + 6.x 与后端同 PR。
5. 同步 `docs/prd/platform/SSE流式数据设计.md` §2.2 事件表。
6. `uv run pytest`；`pnpm lint`。

**回滚**：停用 collector 与发帧；旧前端忽略未知 type。

## Open Questions

- `citation_fallback` Top-K 首版硬编码 5（非阻塞）。
