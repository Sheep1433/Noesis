## Context

见 [README](./README.md) 与 [PRD](../../../docs/prd/knowledge-base/知识库RAG底座详细设计.md)。

## Goals / Non-Goals

**Goals:**

- **DeepDoc 移植**：解析 + 结构感知分块（Phase 1 template `general`）
- **检索自研**：`KbRetrievalService` + MySQL 配置 + rerank
- **预留**：`chunk_template_id` 对接 RAGFlow 更多模板

**Non-Goals:**

- RAGFlow 微服务全栈、GraphRAG、Qdrant 替换

## Decisions

### 1. 为何移植 DeepDoc 而非继续 markitdown？

| 维度 | markitdown + 标题切 | DeepDoc 移植 |
|------|---------------------|--------------|
| 复杂 PDF / 扫描件 | 弱 | OCR + Layout + TSR |
| 表格 / 多栏版式 | 易丢结构 | 专为版式设计 |
| 文档类型扩展 | 需逐个换 parser | upstream 已覆盖 Office/PDF |
| 运维 | 轻 | 需模型权重；仍 **轻于** RAGFlow 全栈 |

**结论**：现阶段文档可单一，但 **解析分块层** 用 DeepDoc 预留能力；**检索编排层** 继续自研。

### 2. 移植边界（不引入 RAGFlow 服务）

```
移植进 Noesis 仓库：
  kb/deepdoc/          ← RAGFlow deepdoc 源码（Apache-2.0）
  kb/chunk/deepdoc_adapter.py
  kb/document_parse/factory.py

不部署：
  RAGFlow API / Worker / ES / Infinity / 管理 UI
```

检索、集合 CRUD、Agent 工具 **仍走现有** `KbRetrievalService` + Qdrant。

### 3. 入库流水线

```
upload
  → ParserFactory（deepdoc，唯一）
  → DeepDocParseResult
  → DeepDocChunkAdapter（chunk_template_id=general）
  → embed → Qdrant
  → invalidate_cache
```

### 4. 模块与 Spec 映射

| 代码 | Spec |
|------|------|
| `kb/deepdoc/` | `kb-document-parse` |
| `kb/chunk/deepdoc_adapter.py` | `kb-chunking` |
| `kb/chunk/markdown_splitter.py` | `kb-chunking`（str 输入 / 测试） |
| `kb/retrieval/service.py` | `kb-retrieval` |
| `kb_collection_config` | `knowledge-base` |

### 5. 配置

```yaml
kb:
  deepdoc:
    enabled: true
    model_dir: .data/rag/res/deepdoc
  parser:
    default: deepdoc
```

### 6. 合规与 Vendor 维护

- 保留 Apache-2.0 LICENSE / NOTICE
- Pin upstream commit；仓库内 `backend/kb/deepdoc/UPSTREAM.md` 记录 pin 与拉取命令
- **手工修改 `kb/deepdoc/**` 须在 `docs/NOTES.md` 登记**（路径、原因、同步策略），升级 RAGFlow 前必读

### 7. 检索链 / MySQL

不变，见 [README § 参数字段](./README.md#参数字段-canonical-名全链路统一)。

## Migration

1. 引入 `kb/deepdoc/` + 模型下载脚本
2. 新上传经 DeepDoc；旧分片仍可检索

## Open Questions

- DeepDoc 权重是否随 Docker 镜像打包 vs 首次启动下载
- GPU 节点是否仅 ingest _worker 需要（若后续异步 ingest）
