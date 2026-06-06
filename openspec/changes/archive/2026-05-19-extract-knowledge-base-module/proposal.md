## Why

当前知识库能力分散在 `qdrant_service`、`retrieval_util`、`knowledge_base_api` 与多个 Agent 的 RAG 调用中，检索策略（向量 / BM25 / 混合）虽已在 `Retrieval` 类实现，但缺少**统一、可复用的对外检索契约**，且 BM25 索引依赖进程内内存重建，不利于多实例部署与独立演进。此外，**元数据过滤**（按文档名、标题层级 `Header_*` / `header_path`）在 `retrieval_util` 中已有实现，但 HTTP 检索 API 未暴露；主上传路径 `QdrantService.upload_document` 在 `markdown_headers` 分块后**未将标题 metadata 写入 Qdrant payload**，导致按标题过滤无法生效。将知识库抽象为独立模块并补齐入库与检索契约，可降低主应用与 Agent 的耦合，并支持「指定集合 + 条件过滤」的 RAG 检索。

## What Changes

- 新增 **`kb-retrieval`** 能力：定义统一的检索服务边界（`vector` / `bm25` / `hybrid`），对外提供 HTTP 检索 API（及可选的内部 Python SDK 门面）。
- 将现有入库、分片、集合元数据与检索逻辑**收敛到独立模块目录**（如 `backend/kb/` 或同级可部署包），主应用通过薄适配层调用，而非 Agent 直接依赖 `QdrantService` / `Retrieval` 实现类。
- 检索 API 请求体显式支持 `search_mode`，响应统一返回分片 id、score、payload 摘要及检索来源（vector / bm25 / hybrid）。
- 检索 API 支持可选 **`filters`**（元数据过滤）：至少包含 `file_name` / `source_name` 精确匹配，以及 `Header_1`~`Header_4`、`header_path`（见 design：精确 vs 路径前缀）；检索范围仍由路径参数 `collection_name` 指定知识库（集合）。
- **入库分块（单一方案）**：上传一律按 Markdown 标题分块，将 `Header_*`、`header_path` 等写入 Qdrant payload；不再提供滑窗 `default` 策略的用户配置。
- **技术选型决策**（见 `design.md`）：在 Milvus / Qdrant / PostgreSQL 中选定主存储方案；首版不引入第二套向量库并行运行。
- 管理类 API（集合 CRUD、上传、状态）可暂保留在 `/api/knowledge_base/*`，但检索路径 SHALL 委托新模块；长期可将管理 API 一并迁入同一模块边界。
- 不删除现有 Qdrant 数据；迁移以适配层与接口对齐为主，**无 BREAKING** 的对外管理 API 变更（检索新端点为增量能力）。

## Capabilities

### New Capabilities

- `kb-retrieval`: 独立知识库检索模块——统一检索门面、三种检索模式、**元数据过滤**、集合级默认参数合并、可观测的错误码与维度校验。

### Modified Capabilities

- `knowledge-base`: 扩展检索与入库相关要求——检索端点支持 `search_mode` 与 `filters` 并委托 `kb-retrieval`；上传流水线在 `markdown_headers` 下持久化标题层级 metadata；BM25 / 混合检索与过滤组合从「仅 `retrieval_util` 内部」提升为对外可验收能力。

## Impact

- **后端**：`backend/services/qdrant_service.py`、`backend/core/retrieval_util.py`、`backend/api/knowledge_base_api.py`、`backend/agent/*/rag*`、`backend/schemas/knowledge_base_schema.py`、`backend/config/env.py`。
- **前端**（可选二期）：`frontend/src/api/knowledgeBase.ts`、知识库调试页可增加检索模式选择。
- **基础设施**：知识库仅依赖 Qdrant（选型见 design）；MySQL 仍用于会话等业务，不参与 KB 元数据。
- **测试**：`docs/test/test_tdd_design.md` 需补充检索模式矩阵用例；`openspec/specs/knowledge-base/spec.md` 通过 delta 对齐。
