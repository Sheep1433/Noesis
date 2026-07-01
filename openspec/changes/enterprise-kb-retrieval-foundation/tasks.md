## 1. 数据模型与集合配置

- [x] 1.1 `kb_collection_config` 表 + Service
- [x] 1.2 collection 生命周期同步
- [x] 1.3 现有集合回填

## 2. DeepDoc 移植（kb-document-parse）

- [x] 2.1 从 RAGFlow pin commit，拷贝 `deepdoc/` → `backend/kb/deepdoc/`，添加 LICENSE/NOTICE + `UPSTREAM.md`
- [x] 2.1b 在 `docs/NOTES.md` 初始化 **DeepDoc vendor 修改清单**
- [x] 2.2 模型下载脚本 / 文档（HuggingFace → `.data/rag/res/deepdoc/`）
- [x] 2.3 `config.yaml`：`kb.deepdoc.enabled/model_dir`
- [x] 2.4 `DeepDocParseResult` 契约 + `ParserFactory`（唯一 `deepdoc`）
- [x] 2.5 PDF/DOCX/EXCEL/PPT 冒烟单测（mock 模型）
- [x] 2.6 解析 JSON 缓存 `.data/kb_parse/`

## 3. 分块（kb-chunking）

- [x] 3.1 `DeepDocChunkAdapter`（`chunk_template_id=general`）
- [x] 3.2 预留 template 枚举 + 未实现回退 general
- [x] 3.3 payload `effective_processing_params`（含 `parser_id`、`deepdoc_version`）
- [x] 3.4 单测：DeepDoc mock 结果 + Excel/Markdown 冒烟

## 4. Rerank

- [x] 4.1 `kb/rerank/client.py` + 降级

## 5. 检索门面（kb-retrieval，自研）

- [x] 5.1 recall → rerank → threshold → final_top_k
- [x] 5.2 MySQL query_params + limit 别名
- [x] 5.3 单测 / 集成测

## 6. HTTP API

- [x] 6.1 config GET/PATCH
- [x] 6.2 上传/检索参数扩展
- [x] 6.3 schema + API 测试（Service/门面单测覆盖）

## 7. Agent

- [x] 7.1 `kb_search_tool` + Case RAG 对齐

## 8. 评测 / 前端 / 文档

- [x] 8.1 `evals/kb`
- [x] 8.2 管理端检索参数 UI
- [x] 8.3 PRD / AGENTS.md / 模型下载 README（`backend/kb/README.md`）

## 9. 部署与 Vendor 维护

- [x] 9.1 Docker：`config.docker.yaml` + `noesis_data` 卷模型路径文档化
- [x] 9.2 合并 main 前 LICENSE 与归属审查（`deepdoc/NOTICE` + `UPSTREAM.md`）
- [x] 9.3 **流程**：任何 `kb/deepdoc/**` 手工 diff → 同步 `docs/NOTES.md` vendor 清单
