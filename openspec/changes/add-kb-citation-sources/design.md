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

## 难度评估（为何引用溯源比「加个 SSE 字段」难）

引用溯源在 Noesis 里被拆成 **四层能力**，难度不均：

| 层级 | 内容 | 难度 | 说明 |
|------|------|------|------|
| **L1 数据** | Qdrant payload 已有 `file_name` / `header_path` / `chunk_index` | 低 | 入库链路成熟 |
| **L2 工程** | `CitationsPart`、snake_case SSE、抽屉调 shard API | 中 | 与 platform-chat 模式一致，可控 |
| **L3 检索定位** | `shard_id` 在 hybrid/BM25 路径可 retrieve | **中高** | 现网 `_doc_to_hit` 与 RRF 合并路径有缝，须修 + 集成测 |
| **L4 语义绑定** | 正文陈述 ↔ 具体分片（cited-only） | **高** | 当前方案 **强依赖 LLM 写 `[n]`**，无后端语义对齐 |

**结论**：本 change **有信心交付 L1–L2 + 刷新后可回放的 L3 兜底**；**L4 的产品体验上限取决于模型是否遵守角标约定**，fallback Top-5 是妥协而非 Perplexity 级「真实引用」。实现前应对业务方对齐：**首版是「结构化来源展示 + 可点击原文」**，不是「可审计的逐句归因」。

**置信度摘要**（实现前心理预期）：

- 落库 / 历史回放 / 正常 finish 的 SSE：**高**
- 点击打开分片（修好 `_doc_to_hit` + hybrid 实测后）：**中高**
- 文内 `[n]` + cited-only 与模型回答一致：**中低**
- 用户 `/stop` 后**流式过程中**立刻看到来源列表：**低**（见 §5.1，首版不保证）

## Goals / Non-Goals

**Goals:**

- 端到端 Citation：登记 → 正文 `[n]` → cited 子集 → SSE（snake_case）→ `CitationsPart` 落库 → UI。
- **completed / partial / stop** 三种终态均尽力写入 `citations` part（保证刷新后可回放）。
- 单测 + **验收级** eval / 手工用例监控 `[n]` 引用率与 `citation_fallback` 占比（见 §9）。

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
| 2 | `VectorStorage.hash_to_uuid(metadata["content_hash"])`（**必须**调用 `store.py` 同一函数：`uuid5(NAMESPACE_DNS, content_hash)`，禁止自写） |
| 3 | 无法解析 → `shard_id=None`，Citation 仍登记；抽屉走文档级 fallback |

**实现要求**：修改 `_doc_to_hit`，`KbSearchHit.id` 按上表解析；**禁止**将裸 `content_hash` 字符串作为 `shard_id` 透出。

**hybrid 注意**：默认 `search_mode=hybrid` 走 RRF，命中可能来自 BM25 分支；BM25 索引文档 **无** `metadata.point_id`，须依赖优先级 2。须加 **`hybrid` 集成测试**（真实 Collection），不能仅测 vector 单路径。历史 payload 缺 `content_hash` 时 `shard_id` 大量为 null，抽屉体验降级为文档级定位——属已知数据债。

`chunk_index`：**硬依赖** — `_doc_to_hit` 从 payload metadata 写入 `KbSearchHit` 新字段；`kb_search_tool` 透传至 hit JSON。

### 3. merged_text 与角标解析

**merged_text 范围**：

- **仅**合并 assistant 回合内 **`type=text` 且 `parent_task_call_id` 为空** 的 part（顶层主回答正文），按 parts 顺序直接拼接（无分隔符）；
- **排除** `reasoning`、`tool`、以及子 Agent 嵌套 text（COMMON_QA 虽无 `task`，规格预先收紧以免误计入）；
- 流式路径：`AssistantMessageBuilder.merged_text_content()` 与落库 parts 语义一致。

**角标解析**（`resolve.py`）：

1. 对 merged_text 先剔除 fenced code block（` ``` ` 与 `~~~` 包裹段），再解析；
2. 正则：`\[(\d{1,3})\](?!\()` — 支持 1–999，排除 Markdown 链接 `[n](url)`；
3. 解析结果 `cited_indices` 仅保留 `1 ≤ n ≤ 50` 且已在 registry 登记的 index；
4. reasoning 内容 **不参与**解析（因 merged_text 不含 reasoning）。

**cited 子集与 fallback**：有角标 → cited-only；无角标但有登记 → score Top-5 + `citation_fallback=true`。

**fallback 语义（产品妥协）**：文末列表 **不代表** 模型逐条引用了这 5 条，仅表示「检索认为相关」；UI 须展示 fallback 说明。若业务要求审计级归因，须二期做正文–snippet 相似度推断（见 §10）。

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

#### 5.1 共享函数与调用顺序（硬约束）

```text
_flush_ctx_text_buffer(ctx, builder)
_finalize_citations(builder, collector)   # 须在 to_dict / 用户停止文案之前
# 正常 finish：bridge 另发 citations-available SSE
snapshot = builder.to_dict()
content = append_user_stop_notice_to_content(snapshot)   # 仅 stop；citations part 已在 snapshot 内
persist(content)
```

`_finalize_citations(builder, collector)`：

1. `merged_text = builder.merged_text_content()`（仅顶层 text parts）；
2. `items, fallback = collector.finalize(merged_text)`；
3. 若 `items` 非空 → `builder.append_citations(...)`（**排在既有 text/tool parts 之后、用户停止提示 text 之前**）。

**CitationCollector 登记位置（硬约束）**：**仅**在 `kb_search_tool._format_hits`（主线程、并行检索 `as_completed` 之后）调用 `register_hits`。**禁止**在 `ThreadPoolExecutor` worker（`_search_one_collection`）内登记——`contextvar` 不会传播到子线程。

**调用点**：

| 路径 | SSE `citations-available` | 落库 `citations` part |
|------|---------------------------|------------------------|
| 正常 `finish`（bridge） | **发出** | 是 |
| `stop_chat` | **首版不保证**（流协程与 stop API 解耦，不向已开 SSE 注入帧） | 是（`status=partial`） |
| 意外断连 `_persist_disconnect_partial` | **不发**（连接已断） | 是（`status=partial`） |

**用户预期**：`/stop` 或断连后，**刷新会话**应能看到来源列表；流式过程中 stop 后立刻出列表 **不是** 首版承诺。

用户停止后刷新：**须**能从 DB 读到 `citations` part。

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

### 9. 验收与 eval（验收门槛，非「可有可无」）

| 类型 | 内容 | 阻塞发布 |
|------|------|----------|
| 单元 | collector、resolve、`citation_id`、SSE golden | 是 |
| 集成 | **hybrid** 检索 → `shard_id` 可 retrieve 或明确 fallback | 是 |
| 手工 | `requirement_docs` 种子入库 → COMMON_QA → `[n]` / 列表 / 抽屉 / **stop 后刷新** | 是 |
| LLM 行为 | 记录 `[n]` 率与 `citation_fallback` 占比 | 记录指标；占比过高须产品评审 |

自动化：`test_citation_e2e_prompt.py` 用固定 assistant 文本（含 `[1]`）断言 finalize，不依赖 live LLM。

### 10. 已知局限与二期方向

| 局限 | 首版处理 | 二期可选 |
|------|----------|----------|
| LLM 不写 `[n]` | fallback Top-5 + 文案 | 正文–snippet 重叠推断 cited |
| 非半角角标 `【1】` | 不支持 | 扩展 resolve |
| inline code 内 `[1]` | 不处理 | Markdown AST 统一 |
| stop 时无流式来源列表 | 刷新 DB 回放 | stream_state 注入 SSE |
| 审计级逐句归因 | 不承诺 | 独立归因或结构化输出 |

## Risks / Trade-offs

- **[Risk] LLM 不写 `[n]`（最高业务风险）** → fallback + 指标；体验可能是「有相关文档」而非「引用了哪句」。
- **[Risk] hybrid/BM25 缺 point_id** → 仅 `VectorStorage.hash_to_uuid`；hybrid 集成测；旧库无 `content_hash` 抽屉降级。
- **[Risk] stop 无 SSE 引用帧** → partial 落库 + stop 后刷新验收。
- **[Risk] contextvar 误用** → 登记只在 `_format_hits` 主线程。
- **[Trade-off] fallback 列表易误解为已引用** → `citation_fallback` UI 强制说明。

## Migration Plan

1. `domain/chat/citations/` + 单测（含 citation_id、resolve、shard_id）。
2. 修复 `_doc_to_hit` + `kb_search_tool` + collector contextvar。
3. `_finalize_citations` 接入 bridge、`stop_chat`、disconnect。
4. 前端 5.x + 6.x 与后端同 PR。
5. 同步 `docs/prd/platform/SSE流式数据设计.md` §2.2 事件表。
6. `uv run pytest`；`pnpm lint`。

**回滚**：停用 collector 与发帧；旧前端忽略未知 type。

## Open Questions

- `citation_fallback` Top-K 首版硬编码 5。
- 二期是否做「无角标时用 snippet–正文重叠推断 cited」——待首版指标（fallback 占比）再定。
