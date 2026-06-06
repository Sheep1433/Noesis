## Context

Noesis 知识库当前形态：

| 层级 | 现状 |
|------|------|
| 存储 | **Qdrant** 为知识库唯一存储（dense 向量 + payload）；集合/文档/检索均不经 MySQL |
| 检索实现 | `core/retrieval_util.py` 中 `VectorStore` + `Retrieval` 已支持 vector / bm25 / hybrid / rrf；**BM25 为进程内 `BM25Retriever`**，依赖 `auto_load_documents` 从 Qdrant 拉回文档重建索引 |
| HTTP | `POST /api/knowledge_base/collections/{name}/search` **仅走向量检索**（直接 `QdrantService.search`），未暴露 bm25 / hybrid，**无 `filters`** |
| 元数据 | `retrieval_util` 支持 `metadata_filter`、`get_content_by_title`；**HTTP 与主上传未接通** |
| 入库断层 | `chunk_text_for_kb(markdown_headers)` 产出带标题的 `Document`，但 `QdrantService.upload_document` 只存 `list[str]`，payload 中 `Header_1`~`4` 恒为空 |
| 调用方 | Agent（`common_react_agent`、`case_generate/rag_retriever` 等）与 API 各自引用不同入口，边界模糊 |

用户希望将知识库**单独抽象成模块**并对外提供统一检索接口。技术选型需在 **Milvus / Qdrant / PostgreSQL** 中落定。

## Goals / Non-Goals

**Goals:**

- 建立 `backend/kb/`（命名可微调）作为知识库**检索与存储适配**的单一模块边界。
- 对外（HTTP + 内部 SDK）提供 `search(collection, query, mode, filters?)`，`mode ∈ {vector, bm25, hybrid}`；`collection` 对应路径参数 `collection_name`（指定知识库）。
- 修复 `markdown_headers` 入库，使 Qdrant payload 含 `Header_1`~`Header_4`、`header_path`、`source_name` 等，供过滤与展示。
- 明确存储选型及 BM25 持久化路径，避免多实例部署时 BM25 索引不一致。
- 主应用与 Agent **仅依赖** `KbRetrievalService`（或等价 Protocol），不直接 `import QdrantService` 做检索。
- 保持现有 Qdrant 数据，首版无强制 re-index；已废弃 `t_kb_collection`。

**Non-Goals:**

- 不引入 Milvus 或与 Qdrant **并行**的第二套向量库。
- 不为「平台级多租户」单独拆微服务进程（首版同进程模块即可；接口形状预留未来 gRPC/HTTP 拆分）。
- 不重做前端知识库管理 UI（检索模式调试可二期）。
- 不迁移业务库 MySQL → PostgreSQL。

## Decisions

### 1. 向量 / 混合检索主存储：**继续使用 Qdrant**

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| **Qdrant** | 已集成；运维轻；支持 dense + **sparse vector**（可持久化 BM25 类稀疏向量）；与现有 `retrieval_util` 一致 | 大规模集群能力弱于 Milvus；BM25 若仍用内存方案则多实例有问题 | **选用** |
| **Milvus** | 大规模、原生 hybrid、生态成熟 | 部署重（依赖组件多）；需全量迁移索引；团队无现网经验 | **否决**（成本 > 收益，与 demo 定位不符） |
| **PostgreSQL**（pgvector + tsvector） | 向量 + 全文同一库；事务一致性好 | Noesis 业务库为 **MySQL**，另起 PG 仅服务 KB 增加运维面；与 Yuxi 全栈 PG 不同，无法复用现有 MySQL 会话模型 | **否决**（除非未来全栈迁 PG，非本变更范围） |

**Rationale：** 迁移到 Milvus/PG 的收益不足以抵消数据迁移与双库运维成本；在现有 Qdrant 上完成模块边界与检索模式暴露即可交付 80% 价值。

### 2. BM25 实现路径：**两阶段，首版内存、二期 Qdrant sparse**

| 阶段 | 做法 | 说明 |
|------|------|------|
| **Phase 1（本变更）** | 沿用 `Retrieval` + 内存 `BM25Retriever`，模块启动或首次检索时对 collection **懒加载** Qdrant points 重建索引 | 与现逻辑一致，快速打通 API；**单实例 / 开发环境**可接受 |
| **Phase 2（后续变更）** | 入库时写入 **sparse vector**（或 Qdrant 全文索引能力），检索走 Qdrant hybrid API | 解决多实例 BM25 不一致；需 re-index 任务 |

不在本变更同时上 Phase 2，但在 `kb` 模块用 **端口适配器**（`Bm25IndexPort`）隔离，避免 API 层感知实现切换。

### 3. 模块边界与目录结构

```
backend/kb/
  __init__.py
  ports.py              # Protocol: KbStorePort, KbRetrieverPort
  retrieval_service.py  # KbRetrievalService：合并 query_params、分发 mode
  adapters/
    qdrant_store.py     # 自 qdrant_service / VectorStore 迁入的薄封装
    memory_bm25.py      # Phase 1 BM25
  schemas.py            # SearchRequest / SearchHit（或复用 knowledge_base_schema 扩展）
```

- **入库 / 集合管理**：由 `services/qdrant_service.py` 承担；`knowledge_base_api` 不依赖 MySQL；**检索路径** 强制经 `KbRetrievalService`。
- **配置**：继续 `config/env.py` 的 `QdrantConfig`；模块内禁止硬编码 host。

### 4. 对外 API 契约

- 扩展 `SearchCollectionBody`：
  - `search_mode: Literal["vector", "bm25", "hybrid"] = "vector"`
  - `hybrid` 使用 **RRF** 融合（`rrf_k` 默认 60，可选请求级覆盖）
  - `filters: Optional[Dict[str, Any]] = None` — 元数据过滤（见下文 §6）
- 响应 `SearchResult` 增加可选字段 `search_mode`、`rank_source`；命中项 MAY 返回 `header_path`、`Header_1` 等摘要字段便于前端展示。
- 路径不变：`POST /api/knowledge_base/collections/{collection_name}/search`，**向后兼容**（缺省 `search_mode` = vector，缺省 `filters` = 不过滤）。
- Agent 内部：提供 `kb.retrieval_service.search(..., filters=...)`，逐步替换 `RAGRetriever` 内联 Qdrant 调用。

### 5. 入库分块：仅 Markdown 标题分块（单一方案）

**问题（已修复）：** 早期存在 `default` 滑窗与 `markdown_headers` 双策略，且上传路径曾未把 `Header_*` 写入 Qdrant。

**决策（简化后）：**

1. **唯一分块方式**：全部文档经 `DocumentParser.split_markdown_with_headers` 按 Markdown 标题层级切分；payload 写入 `Header_1`~`Header_4`、`header_path`、`source_name` 等，与 `VectorStore.add_vectors` 一致。
2. **分块参数不对外暴露**：`chunk_size`、`chunk_overlap`、`strategy` 均为平台常量（代码内 `fixed_processing_params()`）；HTTP/API/UI 均不提供 `processing_params` 与 `processing_params_override`。
3. **解析失败**：仅当 Markdown 标题分块异常时，内部回退滑窗并打日志（实现细节，非用户可选策略）。
4. **历史数据**：`default` 滑窗时期上传的分片无标题 metadata，需 **重新上传** 后方可按 `Header_*` / `header_path` 过滤。

### 6. 元数据过滤语义

**指定知识库：** 由 URL `collection_name` 限定 Qdrant collection，不在 `filters` 里重复传「知识库 id」。

**`filters` 请求体形状（首版）：**

```json
{
  "file_name": "设计书.docx",
  "Header_2": "技术规格",
  "header_path_prefix": "设计书.docx > 第一章"
}
```

| 键 | 语义 | 实现 |
|----|------|------|
| `file_name` / `source_name` | 精确匹配文档 | Qdrant `must` + `MatchValue`（与现 `VectorStore.similarity_search` 一致） |
| `Header_1` … `Header_4` | 该层标题**精确等于**给定字符串 | Qdrant `must`；多键同时出现时为 **AND** |
| `header_path_prefix` | `header_path` 以给定前缀开头（层级路径过滤） | 首版：Qdrant `MatchText`（若版本支持）或 scroll 后过滤；须在 API 文档标明性能差异 |
| 其它键 | 落入 payload 顶层或 `metadata.{key}` | 与现 `metadata_filter` 回退规则一致 |

**检索模式与过滤：**

- `vector` / `hybrid`：向量侧在 Qdrant 应用 `query_filter`；`hybrid` 的 BM25 候选在内存侧 `_apply_metadata_filter`（复用现逻辑）。
- `bm25`：内存 BM25 后过滤，或候选集扩大再过滤（与现 `filter_search` 思路一致）。

**非目标（首版）：**

- 不支持「仅某 Header 层级存在但值为任意」的通配；不支持跨 collection 检索。
- `get_content_by_title`（按标题 scroll 拉全章）可作为门面可选方法或内部工具，**不替代** 语义检索 + `filters` 的 HTTP 契约。

### 7. 混合检索算法

- 混合检索统一为 `Retrieval.rrf_search_with_scores`（RRF 融合）；`hybrid` 与 `rrf_search` 等价。
- 检索默认由代码常量 + 请求体覆盖（`merge_query_execution_params`）；`hybrid` 使用 RRF（`rrf_k` 可请求级覆盖）。

## Risks / Trade-offs

- **[Risk] 多实例下 BM25 索引不一致** → Phase 1 文档与部署说明标注「单 worker 或 sticky」；Phase 2 sparse 纳入 roadmap。
- **[Risk] 大 collection 懒加载 BM25 慢** → 首次检索超时：增加加载超时配置 + 日志；可选预热接口（内部）。
- **[Risk] 模块抽取过程双写/双路径** → tasks 中要求删除 API 层直接 `QdrantService.search` 的检索分支，统一门面。
- **[Risk] 历史分片无标题 metadata** → 文档与 API 说明需 re-upload；过滤无命中时不应 500。
- **[Risk] `header_path_prefix` 在超大 collection 上全表过滤慢** → 优先 Qdrant 索引字段；必要时限制必须同时带 `file_name`。
- **[Trade-off] 不选 Milvus** → 千万级向量、复杂 hybrid 调参场景需未来再评估；当前文档量不适用。

## Migration Plan

1. 新增 `backend/kb/`，将 `retrieval_util.Retrieval` 相关检索逻辑**搬迁或 re-export**，`qdrant_service` 保留连接生命周期（`init_qdrant_client` 仍在 `server.py` lifespan）。
2. 实现入库写全量 payload（`markdown_headers`）；实现 `KbRetrievalService`（含 `filters`）。
3. `knowledge_base_api.search_collection` 改为调用该服务；扩展 schema 与 OpenSpec delta。
4. Agent（`rag_retriever`、`common_react_agent`）逐个改为门面调用，支持传入 `filters`。
5. 验证：`uv run app.py`；对三模式与 metadata 过滤各至少 1 条 API 测试（见 `test_tdd_design.md`）。
6. Rollback：保留旧 `search` 实现为私有函数直至验收通过再删（单 PR 内完成，不长期双方案）。

## Open Questions

- ~~`hybrid` 默认权重~~ **已决**：RRF 融合，`rrf_k` 仅请求级/代码默认，无 per-collection 持久化。
- Phase 2 sparse 是否采用 DashScope 稀疏 embedding 还是经典 BM25 tokenization？（实现前需对照 Qdrant 版本与 embedding 模型文档。）
- `header_path_prefix` 是否在首版强制依赖 Qdrant 全文索引字段，还是允许纯应用层过滤？（实现前确认 Qdrant 版本与 payload 索引配置。）
