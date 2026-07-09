# kb-multimodal-retrieval — 知识库多模态检索调研

> **状态**：调研 / 设计阶段（**未进入实现**）  
> **前置**：[`enterprise-kb-retrieval-foundation`](../archive/2026-07-09-enterprise-kb-retrieval-foundation/README.md)（已归档）文本 RAG 底座  
> **日期**：2026-07-05

## 背景

当前 Noesis 知识库检索为 **纯文本** hybrid（`text-embedding-v4` + BM25 + rerank）。文档内图片仅能通过 OCR 图注或（未接通的）VLM 描述间接参与检索；**无法用自然语言直接召回对应图片 chunk**。会话附件 multimodal 仅作用于对话侧 `ChatAttachmentsMiddleware`，不入 Qdrant。

本 change 沉淀跨模态检索调研与分阶段落地方案，供后续 OpenSpec 规格化与实现。

## 阅读顺序

| 顺序 | 文件 | 内容 |
|------|------|------|
| 1 | [research-report.md](./research-report.md) | 业界路线、模型选型、RAGFlow 对比、工程要点 |
| 2 | [proposal.md](./proposal.md) | 动机、范围、与非目标 |
| 3 | [design.md](./design.md) | Noesis 分阶段架构与 Qdrant schema 草案 |

## 与现有 change 关系

| Change | 关系 |
|--------|------|
| [`enterprise-kb-retrieval-foundation`](../archive/2026-07-09-enterprise-kb-retrieval-foundation/README.md) | 文本入库/检索基线（已归档）；多模态在其上 **扩展** `alt_embedding` 与 `modality` |
| `general-qa-file-upload`（已归档） | 会话图片 multimodal ≠ 知识库跨模态检索 |

## 下一步（未创建）

- `specs/kb-multimodal-retrieval/spec.md`（实现前）
- `tasks.md`（实现任务拆解）
