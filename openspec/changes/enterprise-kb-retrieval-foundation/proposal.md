## Why

Noesis 知识库已具备 Qdrant 入库、`kb/` 检索门面与 Agent `search_knowledge_base` 工具，但当前底座仍存在明显短板：入库仅固定 `markdown_headers` 分块、解析依赖 markitdown 对复杂 PDF 支持不足、检索默认纯向量且 **未接入已配置的 rerank 模型**、集合级 `processing_params` / `query_params` 未持久化到 MySQL。对比 RAGFlow 等专用 RAG 引擎，企业在「制度/产品/需求/FAQ」类通用知识库场景下更需要 **轻量、可嵌入 Agent 平台、可评测迭代** 的自建底座，而非引入整套 RAGFlow 微服务栈。

本变更一次性落地 **自建企业知识库 RAG 底座最终方案**：增强解析与多 preset 分块、hybrid + rerank 默认检索链、集合参数持久化与可追溯、统一评测入口；**不** vendor RAGFlow / Yuxi 全栈，仅借鉴分块算法语义。

## What Changes

- 新增 **多 preset 分块调度器**（`general` / `qa` / `book` / `laws`，保留 `markdown_headers` 为兼容别名），入库前统一转 Markdown，分块参数三层合并并写入 Qdrant payload `effective_processing_params`。
- 新增 **可插拔文档解析**：`markitdown`（默认）、`docling`（Office）、可选 `mineru`（PDF，由配置启用）；解析结果缓存为集合内 Markdown 产物。
- 检索链升级为 **recall（hybrid RRF）→ cross-encoder rerank → final_top_k**；平台与 Agent 默认 `search_mode=hybrid`、`use_reranker=true`（配置缺省 rerank API Key 时降级为仅 hybrid 并记录 warning）。
- 新增 MySQL 表 **`kb_collection_config`** 持久化集合级 `processing_params`、`query_params`；HTTP API 与 Agent 经统一门面读取，请求体可单次覆盖。
- 新增 **知识库检索离线评测**能力（hit@k / recall@k + 可选 LLM judge），供回归与调参。
- 管理端集合配置 UI：分块 preset、检索参数（hybrid / rerank / top_k）可编辑。
- **不引入**：RAGFlow 独立服务、Neo4j/GraphRAG、RAPTOR、Milvus、ES/Infinity、外部 KB 连接器（Dify/Notion）。
- **不破坏**：`/api/knowledge_base` 路径前缀与既有成功响应形状；旧客户端不传新字段时仍可上传与检索。

## Capabilities

### New Capabilities

- `kb-chunking`：多 preset 分块调度、`processing_params` 合并语义、分块单一入口与 payload 追溯。
- `kb-retrieval`：hybrid 默认、recall/rerank 两阶段检索、集合 `query_params`、统一门面与 Agent/API 一致性。
- `kb-document-parse`：解析器工厂、Markdown 中间态、按 `parser_id` 与文件类型选择解析实现。
- `kb-evaluation`：知识库检索基准数据集、hit@k/recall@k 评测 CLI 与报告格式。

### Modified Capabilities

- `knowledge-base`：集合配置持久化、上传 API 支持 `processing_params`、检索 API 扩展 rerank/recall 字段、默认检索模式变更。
- `agent-common-qa`：`search_knowledge_base` 读取集合 `query_params` 并启用 rerank 两阶段召回。
- `agent-test-case`：场景级 RAG 通道与平台检索默认及 `query_params` 对齐。

## Impact

| 区域 | 影响 |
|------|------|
| 后端 `kb/` | `chunk/` 新增 presets + dispatcher；`document_parse/` 工厂化；`retrieval/` rerank 集成；新增 `kb/rerank/` |
| 后端 Service/API | `services/qdrant_service.py`、`api/knowledge_base_api.py`、`schemas/knowledge_base_schema.py` |
| 数据库 | 新增 `kb_collection_config`（Alembic/SQL 脚本）；集合创建/删除时同步行 |
| Agent | `agent/tools/kb_search_tool.py`；`agent/case_generate/` 场景 RAG 召回参数 |
| 配置 | `config.yaml` `rerank` 段接入检索链；可选 `kb.parser.mineru_*` |
| 前端 | 知识库集合管理：分块与检索高级参数表单 |
| 评测 | `backend/evals/kb/` 或 `evals/` 下新入口 |
| 测试 | `backend/tests/test_kb_*`、检索/分块/rerank 单测与 API 集成测 |
| 依赖 | 可选 `docling`；MinerU 以 sidecar HTTP 或 CLI 配置接入，非硬依赖 |
