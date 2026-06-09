## 1. 调研与数据落点确认

- [x] 1.1 清点现有集合/文档元数据的存储位置（MySQL 模型、Qdrant payload、仅配置文件等），在设计 Open Question 的结论上选定 **processing_params / query_params** 的唯一主存与回填策略。
- [x] 1.2 对齐 `schemas/knowledge_base_schema.py` 与 `api/knowledge_base_api.py`，列出须扩展的可选字段清单及向后兼容断言。

## 2. 参数合并与单测

- [x] 2.1 在后端新增或扩展工具模块实现 `deep_merge` + `resolve_effective_processing_params(collection, document, request_once)`（命名以代码审查为准），与 `proposal`/`rag-chunking-pipeline` 规格一致。
- [x] 2.2 为合并优先级与「单次请求不持久化覆盖」编写 `backend/tests/` 单元测试。

## 3. 分块入口与追溯

- [x] 3.1 抽取或封装统一分块入口（如 `chunk_text_for_kb`），当前实现可走现有 `document_util` 逻辑；对未知 `strategy` 行为与规格「降级 vs 报错」在代码注释与 `openspec` 实现说明中保持一致。
- [x] 3.2 入库写 Qdrant 时写入 **effective_processing_params** 快照至 point payload（或等价分片详情来源），并通过既有「分片详情」接口可追溯（老数据可无字段）。

## 4. 检索默认参数

- [x] 4.1 在 `qdrant_service` / `core/retrieval_util.py`（以实际调用链为准）读取集合级 `query_params`，未传参时使用默认值。
- [x] 4.2 General QA / RAG 调用链（如 `common_react_agent`、`qa_service`）确认透传且不破坏现有 sse 契约。（当前 General QA 仍为本地 Markdown `search_knowledge_base`，未走 Qdrant 管理 API，无行为变更。）

## 5. HTTP 层与兼容性

- [x] 5.1 为集合 CRUD / 上传相关端点增加**可选** body 字段，旧客户端不传时行为与现今一致。
- [x] 5.2 向量维度与 collection 不匹配时的 **409 / 约定冲突响应** 在 API 层与 Service 边界落实，并与 `CLAUDE.md` HTTP/业务码规范一致。

## 6. 验证与文档

- [x] 6.1 运行 `uv run app.py` 确认进程可拉起；必要时补充 `backend/tests/` 或对 Qdrant 的契约测试。
- [x] 6.2 `test_tdd_design.md`（若启用）增补本变更涉及的测试点；准备归档时合并 `openspec/specs/knowledge-base/spec.md` 与新增 `rag-chunking-pipeline` 主规格（由 `/opsx:archive` 流程执行）。

## 7. （可选二期）类 RAGFlow 多 Parser

- [x] 7.1 已落地轻量策略 **`markdown_headers`**：`chunk_text_for_kb` 在 `strategy=markdown_headers` 时复用 `DocumentParser.split_markdown_with_headers`；失败或非空块不可得时回退滑窗。未整包 vendor 第三方多 parser 目录，后续可继续按文档类型扩展分支。
