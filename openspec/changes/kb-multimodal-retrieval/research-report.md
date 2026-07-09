# 知识库多模态检索调研报告

> **归档位置**：`openspec/changes/kb-multimodal-retrieval/research-report.md`  
> **关联变更**：`kb-multimodal-retrieval`（proposal / design）  
> **调研方式**：Noesis 代码库、`enterprise-kb-retrieval-foundation` spec、RAGFlow upstream/issue、CLIP/SigLIP/ColPali 论文与工程实践、百炼多模态 embedding 文档  
> **日期**：2026-07-05

---

## 1. 执行摘要

| 结论 | 说明 |
|------|------|
| Noesis 现状 | 知识库 RAG **仅文本向量**；PDF figure 只入库 OCR 图注文字；VLM 图描述链路未接通 |
| RAGFlow 现状 | 主线同为 **VLM caption → 文本 embedding**；原生图向量在 [issue #15761](https://github.com/infiniflow/ragflow/issues/15761) 提案中，**未合入** |
| 文搜图正确做法 | 文本 query 与图片 corpus 须落在 **同一多模态 embedding 空间**（CLIP/SigLIP/百炼 `tongyi-embedding-vision-*` 等） |
| 生产推荐 | **双路检索 + RRF**：文本索引（caption/OCR）+ 图片向量索引（`alt_embedding`）；可选 ColPali 作文档页 rerank |
| Noesis 栈契合 | 已用 DashScope；`tongyi-embedding-vision-plus` / `qwen3-vl-embedding` 独立向量模式可直接做跨模态 ANN |

---

## 2. Noesis 现状

### 2.1 知识库入库与检索

```
upload → DeepDoc parse → chunk → text-embedding-v4 → Qdrant
query  → text-embedding-v4 + BM25 hybrid → rerank → top_k
```

- 嵌入：`backend/kb/embedding/embedding.py` → `get_embedding()`，仅文本。
- 检索：`KbRetrievalService.search()` → vector / bm25 / hybrid，无 `modality` 分支。
- Payload：`element_type=image` 字段存在（`kb/retrieval/payload.py`），但入库路径几乎不产生有效 image chunk。

### 2.2 图片在解析层的命运

| 来源 | 实际入库内容 | 图片本体 |
|------|-------------|----------|
| PDF figure | `deepdoc_service._parse_pdf` 只取 `box["text"]`（图注/OCR） | `box["image"]` **丢弃** |
| DOCX 内嵌图 | `_parse_docx` 不提取图片，`figures=[]` | 未入库 |
| Markdown data URI | `process_images` 在 `parse_file` 中被忽略 | 未入库 |
| VLM 描述 | `enhance_docx_to_markdown` 存在，**未接入 KB 主路径** | — |

Vendored `VisionFigureParser`（`kb/deepdoc/parser/figure_parser.py`）依赖未移植的 `rag.app.picture`；`_ragflow_compat` 中 vision prompt 为空桩。

### 2.3 对话侧多模态（≠ 知识库检索）

- `ChatAttachmentsMiddleware`：`before_agent` 注入 `image_url`，供 Vision LLM 直接看图。
- 范围：会话附件；**不写入 Qdrant**，不参与 `search_knowledge_base`。

### 2.4 相关代码与 spec

| 类型 | 路径 |
|------|------|
| 检索门面 | `backend/kb/retrieval/service.py` |
| 入库 | `backend/services/qdrant_service.py` |
| Payload | `backend/kb/retrieval/payload.py` |
| DeepDoc 适配 | `backend/kb/chunk/deepdoc_adapter.py` |
| KB 底座 spec | `openspec/specs/knowledge-base/` 等（归档自 `enterprise-kb-retrieval-foundation`） |

---

## 3. 核心原理：共享向量空间

「用文字召回图片」的本质：

1. **入库**：图片 → Image Encoder → `v_image`
2. **查询**：文字 → Text Encoder（**同一模型族**）→ `v_text`
3. **召回**：`cosine(v_text, v_image)` 或 ANN 近邻

对比学习（CLIP、SigLIP）在训练时拉近配对 (图, 文)，推远非配对，使两种模态映射到同一超球面。

**反例**：`text-embedding-v4` 编码的 query 与任意图片向量 **不可比**——文本塔与视觉塔不在同一空间。

---

## 4. 三条主流技术路线

### 4.1 路线 A：模态转换（Modality Conversion）

```
图片 → VLM 生成描述 → 文本 chunk → 文本 embedding → 文本索引
```

| 维度 | 评价 |
|------|------|
| 代表 | RAGFlow `rag/app/picture.py`、`VisionFigureParser`；Noesis 未接通 |
| 文搜图 | **间接**，依赖描述是否覆盖图内语义 |
| 优点 | 复用现有文本 RAG；与 hybrid/rerank 无缝 |
| 缺点 | 入库慢（每张图调 VLM）；细节丢失；「找那张架构图」类 query 易漏召 |

RAGFlow 官方在 [issue #8750](https://github.com/infiniflow/ragflow/issues/8750) 确认：**原始图片 embedding 不作为可检索 chunk**。

### 4.2 路线 B：原生跨模态向量（Dense Embedding）

```
图片 → 多模态 embedding（image tower）→ alt_embedding 索引
query → 多模态 embedding（text tower）→ 同一索引 ANN
```

| 维度 | 评价 |
|------|------|
| 代表 | CLIP、SigLIP、Jina-CLIP-v2、百炼 `tongyi-embedding-vision-*` |
| 文搜图 | **原生支持** |
| 优点 | 入库快；适合图库、截图、产品图 |
| 缺点 | 单向量压缩整图，粒度粗；领域图（医疗、CAD）可能需微调 |

### 4.3 路线 C：Late Interaction

```
文档页/图 → 多 token 向量（每 patch 一向量）
query → 多 token 向量
打分 → MaxSim（ColBERT 式 token 级交互）
```

| 维度 | 评价 |
|------|------|
| 代表 | ColPali、ColQwen2.5 |
| 文搜图 | 强，尤其 **PDF 整页**（表格+图+排版） |
| 优点 | ViDoRe 文档检索 SOTA；可省 OCR 管线 |
| 缺点 | 存储与算力高；难直接做亿级 ANN；通常作 rerank 或页级索引 |

### 4.4 生产常见组合

```
Stage 1 召回：SigLIP / 百炼多模态 embedding，ANN top-100（跨文本+图双索引 RRF）
Stage 2 精排：ColPali MaxSim 或 VLM rerank → top-10
Stage 3 生成：VLM 读原图 + 文本 chunk 作答
```

业界 benchmark（ViDoRe）显示 dense 召回 + late-interaction rerank 比单模型 NDCG@10 高约 23%。

---

## 5. 三种端到端架构（Lenovo 2026 多模态 RAG 基准）

| 架构 | 入库 | 检索 | 生成 | 文搜图强度 |
|------|------|------|------|-----------|
| **1** Summarize → Text RAG → Text LLM | VLM 摘要每张图 | 文本 hybrid | 纯文本 | 弱 |
| **2** Summarize → Text RAG + 保留原图 → VLM | VLM 摘要 | 文本 hybrid | VLM 看原图 | 中 |
| **3** Multimodal Embed → Cross-modal ANN → VLM | 只 embed 图 | 图片向量 ANN | VLM 看原图 | **强** |

- **架构 2**：RAGFlow 思路的增强版；Noesis 较易落地（caption 检索 + 命中后返回 `image_uri`）。
- **架构 3**：「用户问一句话，直接命中那张图」；需 `alt_embedding` 与多模态模型。

---

## 6. 模型选型

### 6.1 通用图库 / 自然场景 / 产品图

| 模型 | 维度 | 特点 | 部署 |
|------|------|------|------|
| SigLIP-SO400M | 1152 | dense 检索性价比首选 | 本地 GPU |
| CLIP ViT-L/14 | 768 | 生态最广 | 本地 |
| Jina-CLIP-v2 | 1024 | 多语言 caption 对齐 | API / 本地 |
| **tongyi-embedding-vision-plus** | 1152 | 中英文；与 Noesis DashScope 栈一致 | API |
| **tongyi-embedding-vision-flash** | 768 | 更便宜 | API |
| qwen3-vl-embedding | 可配 | 独立向量 + 融合向量双模式 | API |
| multimodal-embedding-v1 | — | 独立向量，每模态各一向量 | API |

**文搜图**须用模型的 **独立向量（independent embedding）**：text 与 image 各 encode，在同一空间比相似度。

**融合向量（fused embedding）** 适合「图+说明绑一起」的商品检索，不适合「纯文字搜纯图片库」。

参考实现：[VisionText](https://github.com/ankitjosh78/VisionText)（SigLIP + Qdrant）、[CLIP-database](https://github.com/droon/CLIP-database)（SigLIP2 + sqlite-vec）。

### 6.2 文档 / PDF 页面 / 图表密集

| 模型 | 机制 |
|------|------|
| ColPali v1.3 | 整页图像 → 多 token 向量；query token MaxSim |
| ColQwen2.5 | 同上，精度更高 |

适合 DeepDoc 已裁出的 PDF figure 或 **按页** 建索引。

### 6.3 与现有 BGE 文本空间对齐（可选）

**BGE-SigLIP**：SigLIP-2 视觉塔对齐到 BGE-M3 1024 维，便于未来文本 chunk 与图片 chunk 共用空间评估。

### 6.4 Noesis 推荐首选

| 场景 | 推荐 |
|------|------|
| 快速验证 / 与现有配置一致 | DashScope `tongyi-embedding-vision-plus` 或 `flash` |
| 离线 / 成本敏感 | 本地 SigLIP-SO400M |
| PDF 财报级精排 | ColPali 作 Stage-2 rerank（后续阶段） |

---

## 7. 工程实现要点

### 7.1 双索引 + RRF（推荐）

| 索引字段 | 内容 | Query 编码 |
|----------|------|-----------|
| `embedding`（现有） | caption、OCR、上下文文字 | `text-embedding-v4` |
| `alt_embedding`（新增） | 图片像素语义 | 多模态模型 text tower |

检索：两路各 top-N → **RRF 合并**（勿直接比两路 raw cosine，分布不一致）。

Qdrant 实现选项：

- **Named vectors**：同一 point 上 `text` + `image` 两个 vector；
- **双 collection**：`kb_images_{collection}` 专存图 chunk；
- **单字段 `alt_embedding`**：仅 image chunk 非空（RAGFlow #15761 方案）。

无多模态配置的集合：`alt_embedding = null`，检索行为与现网 **完全一致**。

### 7.2 Chunk  schema 扩展（概念）

| 字段 | 说明 |
|------|------|
| `modality` | `text` \| `image` \| `table`（可选 `page`） |
| `alt_embedding` | 图片向量（nullable） |
| `image_uri` | 对象存储或 `.data/kb_images/...` 路径 |
| `caption` | OCR 图注 + 可选 VLM 描述（辅助文本路与展示） |
| `parent_file_hash` | 关联源文档 |

### 7.3 入库流水线（架构 3 最小闭环）

```
DeepDoc 裁图 / 独立图片上传
  → 存 image_uri
  → MultiModalEmbedding(image) → alt_embedding
  → 可选 VLM caption → embedding（文本路）
  → upsert Qdrant（named vectors）
```

### 7.4 查询流水线

```
用户: "第三季度的收入趋势图"
  → mm_embed(text=query) → v_q
  → ANN on alt_embedding, top-50
  → （可选）text hybrid on caption, top-50
  → RRF → rerank → top_k
  → 返回 caption + image_uri（前端/Agent 可渲染原图）
```

### 7.5 已知坑

| 问题 | 对策 |
|------|------|
| Modality Gap | query 模板（`"a photo of {q}"`）、分数校准、两阶段 rerank |
| 粒度 | 一图一向量 vs 整页 ColPali；按业务选 |
| 分数不可比 | 跨模态勿混比 raw score；用 RRF 或分路归一化 |
| 存储 | 向量在 Qdrant；图片本体在对象存储，payload 只存 URI |
| 领域图 | 评估 recall@k；必要时领域微调或 caption 双路兜底 |

---

## 8. RAGFlow 对比与借鉴

### 8.1 当前 RAGFlow 主线

- 解析：DeepDoc OCR + Layout + TSR
- 图片：`vision_llm_chunk` 生成描述 → **文本 embedding**
- 检索：单 `embedding` 字段；全文 + 向量 hybrid

### 8.2 提案 [#15761](https://github.com/infiniflow/ragflow/issues/15761)（未实现）

- Chunk 增加 `modality`、`alt_embedding`
- `clip_embedder.embed_image()` 与 caption 并行
- `Dealer.search()` 双 KNN + RRF
- Query 侧 CLIP text tower 编码

Noesis 可 **对齐该 schema 思路**，但用 DashScope 替代自托管 CLIP，检索仍走 `KbRetrievalService`。

### 8.3 RAGFlow 2025 博客观点

两种路径：

1. **Modality Conversion**（当前主流）：OCR/VLM → 文本 RAG
2. **Native Multimodal**：统一多模态 encoder，late interaction（ColPali）

---

## 9. 与 Noesis 其他能力边界

| 能力 | 边界 |
|------|------|
| `chat-session-attachments` | 会话图片不入 KB；multimodal 仅对话 |
| `enterprise-kb-retrieval-foundation` | 文本 RAG 基线；本调研为其扩展 |
| VLM（`is_vlm_configured`） | 可用于 caption（架构 2）或回答阶段看图，与 embedding 模型职责分离 |

---

## 10. 参考资料

| 来源 | 链接 |
|------|------|
| RAGFlow 是否支持多模态检索 | https://github.com/infiniflow/ragflow/issues/8750 |
| RAGFlow 多模态 chunk 提案 | https://github.com/infiniflow/ragflow/issues/15761 |
| RAGFlow 2025：RAG to Context | https://ragflow.io/blog/rag-review-2025-from-rag-to-context |
| ColPali 论文 | https://arxiv.org/html/2407.01449v2 |
| 百炼向量化（多模态） | https://help.aliyun.com/zh/model-studio/embedding |
| DashScope MultiModalEmbedding SDK | https://github.com/dashscope/dashscope-sdk-python |
| Lenovo 三种多模态 RAG 架构基准 | https://lenovopress.lenovo.com/lp2371-end-to-end-latency-benchmarking-for-three-multimodal-rag-architectures |
