## Context

Noesis 知识库当前形态（见 `openspec/specs/knowledge-base/spec.md`）：

- **存储**：Qdrant 为集合/向量唯一数据源；BM25 为进程内 `rank_bm25` + jieba，经 `kb/retrieval/store.py` 与向量做 RRF 融合。
- **入库**：`kb/document_parse/parser.py` → markitdown/表格行 → `kb/chunk/chunker.py` 仅 `markdown_headers` → `services/qdrant_service.py` 写点。
- **检索**：`KbRetrievalService`（`kb/retrieval/service.py`）支持 vector/bm25/hybrid；HTTP 与 Agent 默认不一致（API 默认 vector，Agent 工具固定 hybrid）；`config.yaml` 已声明 `rerank` 模型但未接入检索链。
- **参数**：`kb/chunk/params.py` 已有 `resolve_effective_processing_params` / `merge_query_execution_params`，但集合级配置 **未落 MySQL**，上传 API 使用 `fixed_processing_params()`。

本设计在 **不引入 RAGFlow 服务** 前提下，将上述能力补齐为通用企业知识库可长期维护的底座，并与 LangGraph Agent（COMMON_QA、测试用例场景 RAG）共用同一门面。

## Goals / Non-Goals

**Goals:**

- 提供 **解析 → Markdown → preset 分块 → 嵌入 → hybrid 召回 → rerank → 引用** 的完整自建链路。
- 集合级 `processing_params` / `query_params` **MySQL 持久化**，HTTP 与 Agent **同一合并语义**。
- 分块借鉴 RAGFlow/Yuxi 的 **General/QA/Book/Laws** 规则，在 `kb/chunk/presets/` 自研实现（不 copyleft vendor 整包）。
- 支持 **离线检索评测**，便于调参回归。
- 管理端可配置 preset 与检索参数。

**Non-Goals:**

- 不部署 RAGFlow、Yuxi、Milvus、Elasticsearch、Neo4j。
- 不做 GraphRAG、RAPTOR、chunk 自动关键词/问题生成（后续独立变更）。
- 不做多租户 SaaS 计费、外部数据源同步（Confluence/飞书）。
- 不将 Qdrant 替换为其它向量引擎；不强制 Qdrant 原生 sparse 向量（首版继续 RRF + 进程内 BM25）。

## Decisions

### 1. 模块边界（`backend/kb/`）

| 模块 | 职责 |
|------|------|
| `kb/document_parse/` | `ParserFactory`：`markitdown` / `docling` / `mineru`（可选）→ `ParsedFile` |
| `kb/chunk/presets/` | `general`、`qa`、`book`、`laws`；`dispatcher.chunk_markdown()` |
| `kb/chunk/params.py` | 合并 `processing_params`；`chunk_preset_id` + `chunk_parser_config` |
| `kb/rerank/` | `RerankClient`：读取 `ModelConfig.rerank_*`，批量 `acompute_score` |
| `kb/retrieval/service.py` | 唯一对外门面：recall → rerank → hits |
| `kb/retrieval/store.py` | Qdrant + BM25；不负责 rerank |
| `services/kb_collection_config_service.py` | MySQL CRUD + 与 Qdrant 集合生命周期同步 |
| `evals/kb/` | 基准集加载、hit@k 报告 |

路由层（`api/knowledge_base_api.py`）保持薄，仅解析请求并委托 Service。

### 2. 集合配置持久化（MySQL）

**决策**：新增表 `kb_collection_config`：

- `collection_name` VARCHAR PK（与 Qdrant collection 名一致）
- `processing_params` JSON NOT NULL DEFAULT `{}`
- `query_params` JSON NOT NULL DEFAULT `{}`
- `created_at` / `updated_at`

创建集合时插入默认行；删除集合时删行。读侧 `get_or_create_defaults(collection_name)` 在缺失时回填平台默认。

**备选**：仅存 Qdrant collection metadata —— 不利于管理端列表页未走向量时的审计；否决。

### 3. 分块 preset 与调度

**决策**：

- 对外枚举：`general`（默认）、`qa`、`book`、`laws`；`markdown_headers` 映射为 `general` 的标题增强路径（保留旧行为兼容）。
- 调度入口：`chunk_text_for_kb(markdown, *, file_id, filename, effective_params) -> list[ChunkRecord]`。
- `effective_params` 含 `chunk_preset_id`、`chunk_parser_config`（token 数、分隔符、overlap 等），合并优先级：**集合默认 → 文档覆盖 → 当次 upload 覆盖**。
- 每个 preset 在 `kb/chunk/presets/*.py` 实现，核心算法参考 Yuxi `ragflow_like`（`naive_merge`、法条/书籍 bullet 模式），**不**引入其 Milvus/Neo4j 依赖。

**备选**：继续仅 `markdown_headers` —— 无法满足法规/FAQ 等场景；否决。

### 4. 文档解析

**决策**：

- 入库流水线：**二进制 → ParserFactory → Markdown 字符串 → 分块**。
- 默认 `parser_id=markitdown`；`.pdf` 在配置 `kb.parser.pdf=mineru` 时走 MinerU HTTP/CLI；Office 可选 `docling`。
- 解析产物路径写入文档元数据（MinIO 或本地 `.data/kb_markdown/{collection}/{file_id}.md`），重索引可跳过重复解析。
- MinerU **可选**：未配置时 PDF 回退 markitdown 并 warning。

### 5. 检索链（最终默认）

**决策**：两阶段检索：

```
query
  → hybrid RRF recall（recall_top_k，默认 50）
  → [可选] cross-encoder rerank（use_reranker=true）
  → final_top_k（默认 10）
  → score_threshold 过滤（在 rerank 后应用）
```

平台默认 `query_params`：

```json
{
  "search_mode": "hybrid",
  "use_reranker": true,
  "recall_top_k": 50,
  "final_top_k": 10,
  "score_threshold": null,
  "rrf_k": 60
}
```

- `use_reranker=true` 但 rerank API 未配置：降级 `use_reranker=false`，日志 warning，**不**失败请求。
- HTTP `POST .../search` 未传 `search_mode` 时等价 **hybrid**（行为变更，API 字段兼容）。
- Agent `search_knowledge_base`：每库读取该库 `query_params`，再与工具 `limit` 合并为 `final_top_k`。

**备选**：Qdrant 原生 sparse + dense —— 需重建索引与运维复杂度；首版继续 RRF，保留扩展点。

### 6. Agent 与 API 一致性

**决策**：`KbRetrievalService.search()` 接受完整 `query_execution_params` dict（合并后），返回 `KbSearchHit` 含 `recall_score`、`rerank_score`（若有）、`search_mode`、`chunk_preset_id`（来自 payload）。

COMMON_QA 与测试用例场景 RAG **禁止**直接调 `Retrieval` 类；一律经 `KbRetrievalService`。

### 7. 评测

**决策**：`uv run python -m evals.kb.run --dataset <jsonl> --collection <name>`：

- 输入：`(query, relevant_chunk_ids[])` 或 `(query, gold_file_name, gold_header_path)`
- 输出：Recall@k、Hit@k、MRR（可选）
- 与 `test-case-agent-eval` 的 `rag_hit_at_k` 字段语义对齐，便于共用断言工具。

### 8. 前端

**决策**：在集合详情/设置中增加「分块策略」「检索参数」折叠面板，读写 `GET/PATCH /api/knowledge_base/collections/{name}/config`（新端点）。上传时可覆盖 `chunk_preset_id`（高级选项）。

## Risks / Trade-offs

- **[Risk] 默认 hybrid+rerank 改变旧集合检索排序** → **缓解**：属预期精度提升；提供 `query_params` 显式改回 `vector`；评测对比基线。
- **[Risk] 进程内 BM25 多实例不一致** → **缓解**：文档化单实例假设；入库/删除后 `KbRetrievalService.invalidate_cache`；长期可迁 Qdrant sparse。
- **[Risk] MinerU 依赖重** → **缓解**：可选配置；默认 markitdown。
- **[Risk] preset 移植与 RAGFlow 行为微差** → **缓解**：`kb-evaluation` 基准集 + 单测固定样例文本。
- **[Trade-off] 不做 GraphRAG/RAPTOR** → 换取实现可控与运维简单；关系型问答后续独立变更。

## Migration Plan

1. 执行 DDL 创建 `kb_collection_config`；脚本为现有 Qdrant 集合插入默认配置行。
2. 部署后端：新分块/检索逻辑；旧分片无 `effective_processing_params` 仍可检索。
3. 新上传文档使用新 preset 默认值 `general`；**不强制**全量重索引；管理端提供「重新索引」按钮（已有能力复用）。
4. 配置 rerank API Key 后自动启用精排；无 Key 时 hybrid-only。
5. Rollback：环境变量 `KB_RETRIEVAL_LEGACY_DEFAULTS=1` 恢复 API 默认 `search_mode=vector`、关闭 rerank（仅应急，不写入 spec 长期支持）。

## Open Questions

- MinerU 以 **HTTP sidecar** 还是 **CLI 子进程** 接入，由实现时根据 `deploy/docker-compose.yml` 是否已有服务决定；spec 只要求 `parser_id=mineru` 可配置。
- 集合配置 PATCH 是否需管理员角色 —— 与现有知识库 API 权限模型对齐（实现时查 `knowledge_base_api` 鉴权）。
