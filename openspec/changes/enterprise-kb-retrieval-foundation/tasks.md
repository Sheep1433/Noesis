## 1. 数据模型与集合配置

- [ ] 1.1 新增 `kb_collection_config` 表（`collection_name` PK、`processing_params`、`query_params` JSON、时间戳）及 Alembic/SQL 脚本
- [ ] 1.2 实现 `services/kb_collection_config_service.py`：get / patch / delete_on_collection_drop / ensure_defaults
- [ ] 1.3 `QdrantService.create_collection` / `delete_collection` 同步创建/删除配置行
- [ ] 1.4 为现有 Qdrant 集合编写一次性回填脚本或启动时 `ensure_defaults` 迁移

## 2. 文档解析（kb-document-parse）

- [ ] 2.1 重构 `kb/document_parse/`：引入 `ParserFactory`、`parser_id` 枚举与 `ParsedFile` 契约
- [ ] 2.2 实现 `docling` 解析路径（docx/xlsx/pptx）
- [ ] 2.3 实现可选 `mineru` 解析路径（配置项 `kb.parser.mineru_endpoint` 等），未配置时 PDF 回退 markitdown
- [ ] 2.4 Markdown 产物缓存路径写入文档元数据；重索引复用逻辑
- [ ] 2.5 单测：各 parser 回退与不支持扩展名错误路径

## 3. 分块 preset（kb-chunking）

- [ ] 3.1 新增 `kb/chunk/presets/`：`general`、`qa`、`book`、`laws` 与 `dispatcher.py`
- [ ] 3.2 移植/实现 `naive_merge`、token 计数、法条/书籍 bullet 规则（参考 Yuxi ragflow_like 语义，自研代码）
- [ ] 3.3 扩展 `kb/chunk/params.py`：`chunk_preset_id`、`chunk_parser_config`、三层 `resolve_effective_processing_params`
- [ ] 3.4 统一入口 `chunk_text_for_kb`；`chunker.py` 改为调用 dispatcher
- [ ] 3.5 payload 写入 `effective_processing_params`（含 `chunk_engine_version`）
- [ ] 3.6 单测：`test_kb_chunk_presets.py` 覆盖四 preset + 滑窗回退 + `markdown_headers` 别名

## 4. Rerank 模块

- [ ] 4.1 新增 `kb/rerank/client.py`：读取 `ModelConfig` rerank 配置，批量异步打分
- [ ] 4.2 未配置 API Key 时 `is_rerank_available()` 为 false；调用方降级
- [ ] 4.3 单测：mock rerank API 的顺序变化与失败降级

## 5. 检索门面（kb-retrieval）

- [ ] 5.1 扩展 `KbSearchHit`：`rerank_score`、`recall_score`（可选）
- [ ] 5.2 `KbRetrievalService.search` 实现 recall_top_k → rerank → final_top_k 两阶段
- [ ] 5.3 默认 `search_mode=hybrid`；合并 MySQL `query_params` + 请求覆盖
- [ ] 5.4 更新 `kb/chunk/params.py` 中 `DEFAULT_COLLECTION_QUERY` 与 `merge_query_execution_params` 字段集
- [ ] 5.5 入库/删除后 `invalidate_cache` 调用点审计（`qdrant_service`）
- [ ] 5.6 单测与集成测：hybrid 默认、rerank 开关、filters 与 rerank 组合

## 6. HTTP API（knowledge-base）

- [ ] 6.1 新增 `GET/PATCH /api/knowledge_base/collections/{name}/config`
- [ ] 6.2 上传 API 接受可选 `processing_params`（`chunk_preset_id`、`parser_id` 等）
- [ ] 6.3 检索 API 扩展 `use_reranker`、`recall_top_k`、`final_top_k`；响应含 `rerank_score`
- [ ] 6.4 更新 `schemas/knowledge_base_schema.py` 与 OpenAPI 描述
- [ ] 6.5 API 测试：配置读写、默认 hybrid、向后兼容最小请求体

## 7. Agent 集成

- [ ] 7.1 更新 `agent/tools/kb_search_tool.py`：按集合读取 `query_params` + 两阶段检索
- [ ] 7.2 更新 `agent/case_generate/` 场景 RAG：统一 `KbRetrievalService` 与集合 query_params
- [ ] 7.3 确认 `common_react_agent` 仅经工具检索，无直连 Qdrant
- [ ] 7.4 测试：`test_kb_search_tool.py` / 场景 RAG 回归

## 8. 离线评测（kb-evaluation）

- [ ] 8.1 新增 `evals/kb/run.py` CLI 与 JSONL 加载器
- [ ] 8.2 实现 Recall@K、Hit@K 聚合与 JSON 报告输出
- [ ] 8.3 提供示例基准集 `evals/kb/fixtures/sample.jsonl`
- [ ] 8.4 文档：`backend/evals/README.md` 增加 KB 检索评测小节

## 9. 前端管理端

- [ ] 9.1 集合详情页：分块 preset 选择与 `chunk_parser_config` 高级项（chunk_token_num、delimiter 等）
- [ ] 9.2 集合检索参数：hybrid / rerank / recall_top_k / final_top_k
- [ ] 9.3 对接 `GET/PATCH .../config` API；表单校验与默认值展示
- [ ] 9.4 上传高级选项：可选 `chunk_preset_id`（折叠面板）

## 10. 配置、文档与收尾

- [ ] 10.1 更新 `config.example.yaml` / `config.prod.example.yaml`：`kb.parser` 与检索默认说明
- [ ] 10.2 更新 `backend/AGENTS.md` 知识库小节（`kb/` 模块地图）
- [ ] 10.3 记录架构笔记至 `docs/NOTES.md`（自建 KB 底座 vs RAGFlow 决策摘要）
- [ ] 10.4 全量回归：`uv run pytest backend/tests/ -q`；前端 `pnpm lint`（触及文件范围）
- [ ] 10.5 应急开关 `KB_RETRIEVAL_LEGACY_DEFAULTS` 实现与文档（仅运维回滚）
