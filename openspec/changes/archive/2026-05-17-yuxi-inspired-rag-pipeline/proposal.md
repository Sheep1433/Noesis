## Why

对齐同级项目 Yuxi 中已被验证的工程模式（**非**照搬 LightRAG/Milvus/Neo4j）：在 Noesis 现有 **Qdrant + MySQL 知识库 API** 上，补齐「分块与检索参数可追溯、默认值可分层合并」能力不足的问题，便于排障、复现检索效果与渐进增强切块策略；参考 Yuxi 的 `ragflow_like` 分块调度思想与 `KnowledgeBase` 式 **统一检索入口 + 元数据 query 参数** 约定，降低 Agent 与入库链路硬编码。

## What Changes

- 引入 **分块/入库处理参数** 的分层模型：集合（或知识库配置）默认 → 单文档覆盖 →（可选）单次任务覆盖；参数与入库日志、分片元数据对齐，便于审计。
- 将 **检索侧默认参数**（如 `top_k`、相似度阈值等，与现有 Qdrant 封装能力对齐）与集合/会话可配置元数据挂钩，避免仅在 `qa_service` / Agent 内写死。
- （可选、分阶段）增加 **可插拔切块策略** 的扩展点：先支持「单一路径 + 显式参数」；后续可接入类 RAGFlow 的多 parser 调度，**不**在本变更中引入 RAGFlow 服务或 Yuxi 全栈依赖。
- **不**在本变更中要求：PostgreSQL LangGraph Checkpoint、Milvus hybrid、Neo4j 图谱、Dify 外挂；**不**改变现有 `/api/knowledge_base` 路径前缀的既有成功语义，新增字段以兼容为主。

## Capabilities

### New Capabilities

- `rag-chunking-pipeline`：描述分块与入库处理参数的分层、扩展点及与 trace/元数据的一致性要求（借鉴 Yuxi `resolve_chunk_processing_params` + dispatcher 思想，适配 Noesis 技术栈）。

### Modified Capabilities

- `knowledge-base`：在现有 Qdrant 集合/文档/检索要求上，增加「处理参数与检索默认参数」的持久化与使用要求；HTTP 形状以向后兼容方式扩展（新字段可选，旧客户端不受影响）。

## Impact

- **后端**：`services/qdrant_service.py`、`core/document_util.py` 或等价入库路径、`api/knowledge_base_api.py`、`schemas/knowledge_base_schema.py`、`config/env.py`（如有新的全局默认）；若 General Agent 检索走统一封装，可能触及 `agent/common_react_agent.py` / `core/retrieval_util.py`。
- **数据库**：若参数落在 MySQL 元表或集合 `extra`，需迁移或 JSON 字段约定（具体见 design）。
- **前端**：管理端若在集合/文档表单中暴露参数，则 `frontend`  knowledge-base 视图与 API 类型；可分阶段仅用后端默认值完成首版。
- **依赖**：不新增必选重型依赖；可选策略模块保持延迟导入或可关闭。
