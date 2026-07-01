## Why

Noesis 知识库需补齐：**DeepDoc 解析分块**、**hybrid + rerank 检索**、**MySQL 集合配置**、Agent/API 参数一致。

**架构原则**：

- **自研**：检索门面 `KbRetrievalService`、集合配置、Agent 集成、评测
- **移植（非微服务）**：RAGFlow **DeepDoc** 模块 → `kb/deepdoc/`，负责解析与结构感知分块；**不**部署 RAGFlow 全栈

## What Changes

- 移植 **DeepDoc**（PDF/DOCX/EXCEL/PPT、OCR/Layout/TSR）+ `DeepDocChunkAdapter`（template `general`）；**不提供 markitdown 入库降级**
- 检索：recall → rerank → `final_top_k`；MySQL `kb_collection_config`
- **预留** `chunk_template_id` 扩展 RAGFlow 多分块模板

## Non-Goals

- RAGFlow **应用/微服务**、Task 队列、ES/Infinity 替代 Qdrant
- GraphRAG、RAPTOR、独立 KB 仓库

## Capabilities

见 [README](./README.md)。

## Impact

见 [PRD](../../../docs/prd/knowledge-base/知识库RAG底座详细设计.md) 与 [tasks.md](./tasks.md)。
