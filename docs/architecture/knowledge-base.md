# 知识库 RAG 架构

> 状态：Current
> OpenSpec：`knowledge-base`；多模态规划见 active change `kb-multimodal-retrieval`

## 1. 目标

知识库为 COMMON_QA、测试用例生成和其它 Agent 提供可配置、可评测的 hybrid retrieval。管理面负责集合、文档和检索配置；Agent 只通过工具/port 使用检索能力。

## 2. 分层

```text
KnowledgeBase API (server/api/knowledge_base_api.py)
  → noesis.services.knowledge_base_service
      → noesis.knowledge.runtime.knowledge_base (KnowledgeBaseManager)
      → noesis.knowledge.parser / chunking / embedding
      → noesis.knowledge.retrieval.KbRetrievalService
      → noesis.knowledge.implementations.qdrant.QdrantService
      → noesis.repositories.kb_collection_config_repository
          → noesis.storage.postgres.manager.pg_manager
          → noesis.storage.postgres.models.knowledge

noesis.agents.tools.kb_search_tool
  → noesis.knowledge (直接 import：KbRetrievalService / normalize_query_execution_params)
  → noesis.knowledge.runtime.knowledge_base
  → noesis.repositories.kb_collection_config_repository.load_query_params_sync
      → noesis.storage.postgres.manager.pg_manager (sync session)
```

知识库引擎位于核心包的 `noesis.knowledge` 子系统；DB engine、ORM 与 Alembic 位于 `noesis.storage`；集合配置 repository 位于 `noesis.repositories`。`noesis` 禁止 import `server`，`server` 单向调用核心包。`KnowledgeBaseManager` 持有 Qdrant client，并由 FastAPI lifespan 和 eval bootstrap 管理生命周期。`noesis.config.checkpointer`（LangGraph checkpoint，独立库）与 `pg_manager`（业务库）并存。

## 3. 数据

- PostgreSQL：集合配置、用户关系和管理元数据。
- Qdrant：向量、chunk 文本与检索 payload。
- `.data/`：解析缓存、上传 staging 和本地模型数据。

Qdrant payload 至少保留文档身份、`chunk_index`、`content_hash`、标题路径、文件类型和正文。point id 是存储实现细节，不应直接成为公开授权凭据。

## 4. 入库

```text
upload
  → staging
  → ParserFactory / DeepDoc
  → normalized document
  → chunk
  → embedding
  → Qdrant upsert
```

解析失败必须返回明确错误，禁止写空向量。相同内容可通过 hash 去重；处理参数变化后应重新解析和入库。对 `deepdoc/**` 的手工修改必须登记到 `docs/NOTES.md`。

## 5. 检索

`KbRetrievalService` 提供 vector、BM25 和 hybrid 检索，并可执行 rerank。集合配置和请求覆盖共同生成执行参数；结果需经过全局排序、limit 和权限范围限制。

跨集合检索由 KB tool 并行调用单集合检索，再进行全局 Top-K。工具入参不能扩大当前会话/用户允许的 collection scope。

## 6. Agentic RAG 方向

知识库不把整篇文档直接灌入 prompt。Agent 根据问题选择 collection、改写 query、评估检索结果并决定是否再次检索。检索工具向 Agent 提供文件名、Collection、excerpt 和可用 locator；Agent 按共享 system prompt 在普通 Markdown 正文中生成编号引用和参考资料列表。平台另外保存 retrieval part 供结果折叠、刷新恢复和调试，但不把所有检索结果自动认定为正文引用。

引用要求命中 payload 含 `document_id`、`document_version_id` 和 `segment_id`。旧版本入库的 Collection 没有这些字段时，命中仍可供 Agent 阅读，但不会进入可引用 retrieval results；需通过当前入库管线重新上传文档，不执行运行时猜测或历史数据兼容。

## 7. API

正式前缀为 `/api/knowledge_base`，包括：

- 集合查询、创建、删除和配置；
- 文档上传、列表、删除；
- 单集合检索；
- 文档分片与分片详情。

API 只负责认证、参数和响应封装，业务编排位于 `noesis.services.knowledge_base_service`。

## 8. 评测

- `backend/evals/kb/`：单集合 retrieval 指标。
- `backend/evals/case/rag/`：测试用例场景 RAG。
- 默认 pytest 不调用外部模型；live eval 使用显式环境开关。

## 9. 代码入口

- API：`backend/server/api/knowledge_base_api.py`
- Service：`backend/packages/noesis-core/src/noesis/services/knowledge_base_service.py`
- Knowledge：`backend/packages/noesis-core/src/noesis/knowledge/`
- Agent tool：`backend/packages/noesis-core/src/noesis/agents/tools/kb_search_tool.py`
- 配置：`backend/packages/noesis-core/src/noesis/config/yaml_config.py`
