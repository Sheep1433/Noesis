# enterprise-kb-retrieval-foundation — 知识库 RAG 底座（唯一跟踪入口）

> **权威来源**：实现与验收 **一律以本 change + PRD 为准**。
> PRD：[`docs/prd/knowledge-base/知识库RAG底座详细设计.md`](../../../docs/prd/knowledge-base/知识库RAG底座详细设计.md)

## 架构一句话

| 层 | 策略 |
|----|------|
| **解析 + 分块** | **移植 RAGFlow DeepDoc**（`kb/deepdoc/`，库嵌入，非微服务）+ `DeepDocChunkAdapter` |
| **检索 + 配置 + Agent** | **自研** `KbRetrievalService` + MySQL + rerank |

## 能力地图

| 能力 id | 文件 | 职责 |
|---------|------|------|
| `knowledge-base` | [specs/knowledge-base/spec.md](./specs/knowledge-base/spec.md) | HTTP API、MySQL 配置 |
| `kb-document-parse` | [specs/kb-document-parse/spec.md](./specs/kb-document-parse/spec.md) | DeepDoc 移植、ParserFactory（唯一 deepdoc） |
| `kb-chunking` | [specs/kb-chunking/spec.md](./specs/kb-chunking/spec.md) | DeepDocChunkAdapter、template `general`、预留多分块模板 |
| `kb-retrieval` | [specs/kb-retrieval/spec.md](./specs/kb-retrieval/spec.md) | hybrid + rerank 门面（自研） |
| `kb-evaluation` | [specs/kb-evaluation/spec.md](./specs/kb-evaluation/spec.md) | 单集合评测 CLI |
| `agent-common-qa` / `agent-test-case` | 各 spec | 消费方 |

## 阅读顺序

1. [design.md](./design.md) — **为何 DeepDoc 移植 + 边界**
2. `kb-document-parse` → `kb-chunking` → `kb-retrieval`
3. `knowledge-base` → Agent delta

## 参数字段（入库 processing_params）

| 字段 | 默认 | 说明 |
|------|------|------|
| `parser_id` | `deepdoc` | 唯一解析引擎 |
| `chunk_template_id` | `general` | 预留 book/paper/laws 等 |
| `chunk_parser_config` | chunk_size/overlap | 二次切分参数 |

**Vendor 维护**：手工修改 `kb/deepdoc/**` → 必须同步 [`docs/NOTES.md`](../../../docs/NOTES.md)「DeepDoc Vendor 修改清单」。

检索 `query_params` 见下表（不变）：

| 字段 | 平台默认 |
|------|----------|
| `search_mode` | `hybrid` |
| `use_reranker` | `true` |
| `recall_top_k` | `50` |
| `final_top_k` | `10` |
| `limit` | deprecated → `final_top_k` |
