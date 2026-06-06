## 1. 模块骨架与端口

- [x] 1.1 创建 `backend/kb/` 目录与 `ports.py`（`KbRetrieverPort` / 存储适配接口）
- [x] 1.2 从 `core/retrieval_util.py` 抽取或 re-export `Retrieval` / `VectorStore` 至 `kb/adapters/`，保持行为不变
- [x] 1.3 实现 `KbRetrievalService`：合并 `query_params`、分发 `vector|bm25|hybrid`、透传 `filters` 至 `retrieval_util` 等价逻辑

## 2. 入库与标题 metadata

- [x] 2.1 新增 `chunk_documents_for_kb`（或等价）返回带 `Header_*` / `header_path` 的 Document 列表；`markdown_headers` 走 `DocumentParser.split_markdown_with_headers`
- [x] 2.2 重构 `QdrantService.upload_document`（或 `kb` ingest 适配器）：按 `VectorStore` payload 形状 upsert，修复标题字段恒为空问题
- [x] 2.3 在 `docs/prd/modules/知识库设计.md` 注明：历史已上传数据需 re-upload 方可按标题过滤

## 3. Schema 与 HTTP API

- [x] 3.1 扩展 `SearchCollectionBody`：`search_mode`、可选 hybrid 权重、`filters`（含 `header_path_prefix` 等）；扩展 `SearchResult` 含 `search_mode` 及可选 `header_path`
- [x] 3.2 重构 `knowledge_base_api.search_collection`：调用 `KbRetrievalService` 并传入 `filters`，删除内联 `QdrantService.search`
- [x] 3.3 在 `docs/test/test_tdd_design.md` 补充：三模式检索 + `file_name` 过滤 + `Header_2` / `header_path_prefix` 过滤（`markdown_headers` 上传前提下）

## 4. Agent 与调用方迁移

- [x] 4.1 `agent/case_generate/rag_retriever.py` 改为经 `KbRetrievalService` 检索（支持可选 `filters`）
- [x] 4.2 梳理 `common_react_agent` 等其它 RAG 入口，统一门面（无直接 Qdrant 检索；仍使用本地 Markdown 目录搜索）
- [x] 4.3 确认 `qdrant_service` 仅负责连接生命周期与入库/管理，检索逻辑不重复

## 5. 配置、文档与验证

- [x] 5.1 更新 `docs/prd/modules/知识库设计.md`：模块边界、filters 语义、入库策略与 Qdrant 选型
- [x] 5.2 `uv run app.py` 验证启动；pytest/手工覆盖 search 三模式 + 至少 1 条 metadata 过滤
- [x] 5.3 归档前运行 `openspec validate extract-knowledge-base-module`（若项目有该命令）

## 6. 后续（非本变更，仅记录）

- [ ] 6.1 【Phase 2】入库写入 Qdrant sparse vector，BM25/hybrid 改持久化索引（单独 change）
- [ ] 6.2 【可选】bulk re-index 任务：为历史集合回填 `Header_*`（单独 change）
