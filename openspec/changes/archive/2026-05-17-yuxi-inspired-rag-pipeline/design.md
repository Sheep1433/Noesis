## Context

Noesis 知识库已具备基于 Qdrant 的集合、文档上传、分片与检索接口（详见 `openspec/specs/knowledge-base/spec.md`），入库链路集中在后端 Service 与 `document_util` / `qdrant_service` 等模块。对标 Yuxi 的可借鉴点集中在 **三层合并的处理参数**、**检索默认参数与元数据绑定**、**未来可扩展的分块调度入口**，而非引入 LightRAG、Milvus hybrid、Neo4j 或 RAGFlow 独立服务。

## Goals / Non-Goals

**Goals:**

- 在 Noesis 栈内约定 **processing_params（入库分块）** 与 **query_params（检索默认）** 的数据形状与优先级：KB/集合默认值 → 单文档覆盖 →（可选）运行时覆盖。
- 保证入库产生的分片在元数据中能追溯到 **生效后的完整参数快照**（或等价可查询字段），满足排障与复现。
- 通用问答等走 RAG 的路径 SHALL 能够从统一封装读取上述默认检索参数（与现有 embedding 维度、collection 一致性约束相容）。
- 预留 **strategy / dispatcher 扩展点**（模块级函数或 Protocol），第二期可挂载类 RAGFlow 的多_parser 分块，不在首版实现所有 parser。

**Non-Goals:**

- 不引入 Yuxi 的 PostgreSQL Checkpoint、ARQ Worker、Neo4j、Milvus BM25 hybrid、Dify 只读 KB。
- 不将「整块 Yuxi `ragflow_like` 包」原样.vendor；仅借鉴参数合并与扩展点语义。
- 不在本设计中强制重做前端表单；可先后端默认 + API 可选字段。

## Decisions

1. **参数存放位置（优先 PostgreSQL/MySQL 元数据而非仅 Qdrant payload）**  
   - **决策**：集合级默认处理/查询参数存放在应用已有持久化模型（例如集合配置表、扩展 JSON、`extra`），分片入库时将 **effective_params** 写入 Qdrant point payload 或分片明细 API 可查字段，避免仅在内存中生效。  
   - **备选**：仅存 Qdrant payload——不利于列表页未 hit 向量时的审计；否决为主路径。

2. **合并语义（对齐 Yuxi `resolve_chunk_processing_params` 思想）**  
   - **决策**：`effective = deep_merge(collection_defaults, document_overrides)`；单次 ingest 任务的临时覆盖通过 API 可选 body 传入且仅作用于该次任务；合并函数单测固定优先级。  
   - **备选**：仅存一份 JSON 在用户请求体——不利于长期复现；作为补充而非唯一来源。

3. **检索默认值应用点**  
   - **决策**：`qdrant_service` 或 `retrieval_util` 的搜索入口读取 collection 配置的 `query_params.options`（或项目中等价结构），再由 Agent/Service 传入的 kwargs 覆盖；与现有 `top_k`、`score_threshold` 等参数对齐命名。  
   - **备选**：只在 Agent prompt 注入——不可审计；否决。

4. **扩展点形态**  
   - **决策**：首版提供 `chunk_text_for_kb(markdown_or_text, *, effective_params, file_hint) -> chunks` 单入口，内部为「默认策略分支」；`effective_params.strategy`（可选枚举）为未来多策略预留，`unknown strategy` 退回默认并记录 warning。  
   - **备选**：类 Yuxi 完整 dispatcher 目录树——周期长，降为后续变更。

## Risks / Trade-offs

- **[Risk]** 扩展 API 字段导致前后端契约漂移 → **缓解**：Schema 单一来源（`knowledge_base_schema`），可选字段默认为空表示「用服务端默认」。  
- **[Risk]** 历史集合无参数块 → **缓解**：读侧默认与 `migrate`/`ensure_defaults` 在首次读写时回填空对象，行为与现今一致。  
- **[Risk]** 参数组合导致embedding 维度与 collection 不匹配 → **缓解**：创建集合时固化 embed 模型维度；入库前校验 unchanged；文档已说明不适用「换模型不重索引」。  
- **[Trade-off]** 首版不实现 Yuxi 级多 parser → 收益是增量可交付；需在本变更 tasks 中单列二期条目。

## Migration Plan

1. 增加可选 JSON 字段或配置表列（若无则仅存内存默认 + Qdrant payload 追溯，须在产品上接受限制）。  
2. 回填：既有集合 `processing_params={}`、`query_params` 取自代码现有默认。  
3. 入库新路径写入 effective 快照；旧分片可无快照，检索行为不变。  
4. Rollback：关闭「读配置」开关（环境变量）可回退到硬编码默认（仅限开发抢险，不推荐生产长期依赖）。

## Open Questions

- 集合配置是否已在 MySQL 有独立表，或仅用 Qdrant collection metadata；需实现前扫 `model/` 与 `knowledge_base_api` 确定唯一落点（tasks 阶段确认）。  
- 前端是否在首版暴露「高级参数」折叠面板（可推迟）。
