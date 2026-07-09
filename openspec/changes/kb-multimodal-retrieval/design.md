## Context

见 [research-report.md](./research-report.md) 与 [enterprise-kb-retrieval-foundation](../enterprise-kb-retrieval-foundation/README.md)。

当前基线：

- 解析：DeepDoc（`kb/deepdoc/`）
- 分块：`DeepDocChunkAdapter`（`chunk_template_id=general`）
- 检索：`KbRetrievalService`（hybrid → rerank → `final_top_k`）
- 向量：Qdrant 单向量 + 文本 payload

## Goals / Non-Goals

**Goals（分阶段）**

| 阶段 | 目标 |
|------|------|
| **Phase 0**（文档） | 本 change：调研与设计草案 |
| **Phase 1** | 架构 2：VLM caption 入库 + 保留 `image_uri`；文本检索可命中图注；Agent 可读原图 |
| **Phase 2** | 架构 3 最小闭环：`alt_embedding` + 文搜图 ANN + 与文本路 RRF |
| **Phase 3**（可选） | ColPali 文档页 rerank 或页级索引 |

**Non-Goals**

- 见 [proposal.md](./proposal.md)

## Decisions

### 1. 总体策略：在文本 RAG 上扩展，不 fork 第二套 KB

```
                    ┌─────────────────────────────────┐
                    │     KbRetrievalService          │
                    │  recall (text ∥ image) → RRF   │
                    │  → rerank → final_top_k         │
                    └───────────────┬─────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
        text embedding        alt_embedding          BM25 (caption)
     (text-embedding-v4)   (multimodal text tower)   (现有)
```

未配置多模态 embedding 的集合：**仅走现有文本路**，行为与现网一致。

### 2. 多模态 embedding 提供商（Phase 2）

**首选：DashScope 独立向量 API**（与现有百炼栈一致）

| 配置项 | 建议默认 |
|--------|----------|
| `kb.multimodal_embedding.model` | `tongyi-embedding-vision-plus` 或 `flash` |
| `kb.multimodal_embedding.enabled` | `false`（按集合或全局显式开启） |
| API Key | 复用 `EMBEDDING_MODEL_API_KEY` 或独立 `MULTIMODAL_EMBEDDING_API_KEY` |

**备选**：本地 SigLIP（`kb.multimodal_embedding.provider: local_siglip`），用于离线/成本敏感环境。

**职责分离**：

| 模型 | 用途 |
|------|------|
| `text-embedding-v4` | 文本 chunk、caption 文本路 |
| `tongyi-embedding-vision-*` | 图片 `alt_embedding` + 跨模态 query encode |
| VLM（`vlm.*`） | Phase 1 caption 生成；回答阶段看图（可选） |

### 3. Qdrant Schema 草案（Phase 2）

**方案：Named Vectors**（推荐，单 point 可兼有文本与图向量）

```yaml
# 概念配置
vectors:
  text:
    size: 1024    # 与 collection vector_dimension 一致
    distance: cosine
  image:
    size: 1152    # 与 multimodal 模型一致；或统一 pad/投影层
    distance: cosine
```

**Payload 扩展**（与 `build_payload` 对齐）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `modality` | string | `text` \| `image` \| `mixed` |
| `element_type` | string | 保留现有；image chunk 为 `image` |
| `image_uri` | string? | 相对 `.data/kb_images/` 或 collection 内路径 |
| `caption` | string? | OCR + VLM 描述，供 BM25 与展示 |
| `page_no` | int? | PDF 页码 |
| `figure_bbox` | float[]? | 可选，溯源 |

**兼容性**：

- 现有集合仅 `text` named vector（或默认单向量）→ 无迁移强制要求
- 新集合或「启用多模态」的集合创建双 named vector
- 旧 point 无 `image` vector 时，图片路 ANN 自动跳过

**简化备选**（Phase 2 早期）：图片单独 collection `{name}__images`，避免改现有 collection schema；检索时双 collection RRF。文档中标注为 **MVP 垫片**，正式方案仍为 named vectors。

### 4. 入库流水线

#### Phase 1：Caption + image_uri（架构 2）

```
DeepDoc parse_into_bboxes / docx 图片提取
  → 持久化 image bytes → .data/kb_images/{collection}/{file_hash}/{chunk_id}.png
  → 可选 VLM caption（is_vlm_configured）
  → chunk: page_content = caption || OCR text
  → metadata: image_uri, modality=image
  → 现有 text embed + upsert（单向量）
```

**改动点**：

- `deepdoc_service._parse_pdf`：figure 块保留 `image`，写盘 + caption
- 接通 `figure_parser` 或 Noesis 薄封装调 `kb/embedding` VLM
- `deepdoc_adapter`：`figures` 单独 chunk，`modality=image`

#### Phase 2：alt_embedding（架构 3）

在 Phase 1 基础上：

```
  → multimodal_embed(image) → vector image
  → multimodal_embed(caption) 可选，或仅用 text-embedding 写 text 向量
  → upsert named vectors { text, image }
```

### 5. 检索流水线

**`query_params` 扩展**（集合级，合并进 `normalize_query_execution_params`）：

| 字段 | 默认 | 说明 |
|------|------|------|
| `enable_multimodal_recall` | `false` | 是否启用图片向量路 |
| `multimodal_recall_top_k` | `30` | 图片 ANN 候选数 |
| `multimodal_fusion` | `rrf` | `rrf` \| `image_only` \| `text_only` |
| `modality_filter` | `null` | `["text","image"]` 过滤命中类型 |

**`KbRetrievalService.search()` 伪流程**：

```python
if enable_multimodal_recall and collection_has_image_vectors:
    image_vec = mm_embed.encode_text(query)
    image_hits = vector_store.search(vector_name="image", ...)
text_hits = existing_hybrid_recall(...)
merged = rrf_merge(text_hits, image_hits)
reranked = rerank_documents(merged, query)  # 文本 reranker；图 chunk 用 caption 作 passage
return apply_threshold_and_top_k(reranked)
```

**`KbSearchHit` 扩展**：

| 字段 | 说明 |
|------|------|
| `modality` | `text` \| `image` |
| `image_uri` | 可选，前端/Agent 渲染 |
| `recall_source` | `text` \| `image` \| `bm25` |

### 6. Agent 与 API

- `search_knowledge_base` 工具：返回 hit 含 `image_uri` 时，COMMON_QA 可将原图注入 multimodal context（与附件中间件类似，**只读 KB 路径**）
- `GET` 知识库文档预览 API：可选 `image_uri` 签名 URL（实现阶段再 spec）

### 7. 配置草案

```yaml
kb:
  multimodal_embedding:
    enabled: false
    provider: dashscope          # dashscope | local_siglip
    model: tongyi-embedding-vision-plus
    # base_url / api_key 继承 embedding 段或独立 env
  image_storage:
    root: .data/kb_images        # 与 common.paths 对齐
  figure_enrichment:
    vlm_caption: true            # Phase 1：入库时生成描述
    vlm_only_when_no_ocr: false  # 仅无 OCR 文字时调 VLM
```

集合级 `processing_params` 扩展：

| 字段 | 默认 | 说明 |
|------|------|------|
| `index_images` | `true` | 是否从文档提取图片 chunk |
| `multimodal_embedding` | `false` | 是否为图片写 `image` 向量 |

### 8. 评测（实现阶段）

`evals/kb/fixtures/multimodal.jsonl` 样例：

```jsonl
{"query": "第三季度收入趋势", "expected_image": "report_q3_chart.png", "collection": "demo"}
{"query": "系统架构图", "expected_modality": "image", "min_recall_at_5": 1}
```

指标：Recall@k（图 hit）、双路 ablation（仅文本 / 仅图 / RRF）。

### 9. 分阶段交付与依赖

```mermaid
flowchart LR
    P0[Phase 0 文档] --> P1[Phase 1 caption+uri]
    P1 --> P2[Phase 2 alt_embedding+RRF]
    P2 --> P3[Phase 3 ColPali rerank]
```

| 阶段 | 依赖 | 风险 |
|------|------|------|
| Phase 1 | VLM 配置、DeepDoc figure 写盘 | vendor `rag.app.picture` 需垫片或自研 VLM 调用 |
| Phase 2 | 百炼多模态 API、Qdrant named vectors | 文本/图向量维度不一致需配置双 size |
| Phase 3 | ColPali 推理资源 | GPU、延迟 |

### 10. Vendor 与合规

- Phase 1 若修改 `kb/deepdoc/**` 接通 figure：须登记 `docs/NOTES.md`
- 多模态 embedding 模型须在对外文档标注第三方（DashScope / SigLIP），不声称自研

## Open Questions

| # | 问题 | 倾向 |
|---|------|------|
| 1 | Named vectors vs 独立 `__images` collection | 正式用 named vectors；MVP 可用独立 collection |
| 2 | 图片向量维度与文本维度不同 | Qdrant named vectors 原生支持；collection 创建时声明双 size |
| 3 | rerank 对纯图 chunk | 用 `caption` 作 passage 送 text-rerank；或 Phase 3 前跳过 rerank |
| 4 | 是否默认开启 Phase 2 | 默认 `false`，按集合显式开启，控制 API 成本 |

## 相关文件（实现时）

| 模块 | 路径 |
|------|------|
| 多模态 embed | `backend/kb/embedding/multimodal.py`（待建） |
| Payload | `backend/kb/retrieval/payload.py` |
| 检索 | `backend/kb/retrieval/service.py` |
| 入库 | `backend/services/qdrant_service.py` |
| 解析 | `backend/kb/document_parse/deepdoc_service.py` |
| 分块 | `backend/kb/chunk/deepdoc_adapter.py` |
