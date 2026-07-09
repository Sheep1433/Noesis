## Why

企业知识库大量内容以 **图表、架构图、扫描页内插图** 形式存在。当前 Noesis 文本 RAG（`enterprise-kb-retrieval-foundation`）对图片仅保留 OCR 图注或丢弃像素信息，用户无法用「收入趋势图」「登录页截图」等自然语言 **可靠召回对应图片**。会话附件虽支持 Vision multimodal，但不进入知识库索引。

需要在不推翻现有文本 hybrid + rerank 的前提下，明确 **跨模态检索** 的技术路线与分阶段落地边界，避免实现时重复调研或与 RAGFlow 全栈混淆。

## What Changes（本 change 范围）

本 change **仅交付 OpenSpec 文档**（调研 + 设计草案），**不包含代码实现**：

- [research-report.md](./research-report.md)：业界路线、模型选型、Noesis/RAGFlow 现状对比
- [design.md](./design.md)：分阶段架构、Qdrant schema、配置与 API 扩展草案
- 后续实现时再补充 `specs/`、`tasks.md`

## Capabilities（规划，未编写 spec）

### New Capabilities（实现阶段）

- `kb-multimodal-retrieval`：图片 chunk 入库、`alt_embedding`、跨模态 ANN、双路 RRF、命中返回 `image_uri`

### Modified Capabilities（实现阶段）

- `kb-document-parse`：PDF/DOCX 图片提取与 `image_uri` 持久化
- `kb-retrieval`：`KbRetrievalService` 多模态查询参数、`modality_filter`
- `knowledge-base`：集合级 `multimodal_embedding` 配置
- `agent-common-qa`：检索结果含图片时展示/交给 VLM

## Non-Goals

- 替换 Qdrant 或引入 RAGFlow 微服务 / ES / Infinity
- 原生音频/视频跨模态检索（可后续单独立项）
- 自研 CLIP 训练或 ColPali 训练
- 聊天附件自动写入知识库（仍走 `chat-session-attachments` 独立管线）
- 本阶段编写 `tasks.md` 或合并进 `enterprise-kb-retrieval-foundation` 实现

## Impact（实现阶段预估）

| 区域 | 路径 |
|------|------|
| 多模态嵌入 | `backend/kb/embedding/`（新增 multimodal 门面） |
| Payload / 入库 | `backend/kb/retrieval/payload.py`、`qdrant_service.py` |
| 检索 | `backend/kb/retrieval/service.py` |
| 解析 | `kb/document_parse/deepdoc_service.py`、可选接通 `figure_parser` |
| 配置 | `config.yaml` → `kb.multimodal_embedding` |
| 评测 | `evals/kb/` 增加文搜图 case |

## 推荐阅读

1. [research-report.md](./research-report.md)
2. [design.md](./design.md)
3. [enterprise-kb-retrieval-foundation](../enterprise-kb-retrieval-foundation/README.md)
