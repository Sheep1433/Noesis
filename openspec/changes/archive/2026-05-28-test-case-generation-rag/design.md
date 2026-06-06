## Context

- **开源 vs 内网**：内网「两阶段 RAG」第一阶段 = 从知识库检索正确设计文档；开源由用户上传 Word/Markdown 并入库到 `test_case_upload_collection` / `requirement_docs`，**等价于已选定正确文档**，不在本变更实现全库文档路由。
- **阶段 A**：`generate_scenes_testpoints_node` 使用 `document_context`（上传解析正文），产出 `scenes_testpoints`；测试点为**标题级** `point_name` + 元数据，非完整用例。
- **阶段 B（目标形态）**：按**已采纳测试点所属场景**分组；每个场景 **一次 RAG** → 该场景内各测试点**并行**生成用例（共享同一份 `scene_rag_context`）。
- **阶段 B（当前实现，待改）**：对每个测试点单独召回；阶段 A prompt 要求 LLM 输出 `chunk_indexes`，阶段 B 用 `retrieve_chunks` 按整数索引取 Qdrant 分片——易幻觉、与入库顺序强耦合，且同场景重复召回浪费。
- **`chunk_indexes` 说明**：入库时 Qdrant payload 上的逻辑分片序号（`chunk_index`），**不是**测试点业务字段；**SHALL NOT** 由阶段 A LLM 填写并绑定到每个测试点。
- **缺口**：场景级 hybrid 召回、场景并行单元与 `retrieval_trace`（按 `scene_name`）未系统化。

## Goals / Non-Goals

**Goals:**

- **场景级**双路召回（查询文本 = `scene_name` + `scene_description`，可选拼接本场景已采纳 `point_name` 列表作关键词，**不**按测试点逐条查）：
  1. **需求文档**：`requirement_docs`，hybrid Top-K（含种子文档与用户上传）；
  2. **历史测试用例**：`test_case_docs_collection`，hybrid Top-K。
- 并行粒度：**每个场景一个 asyncio 任务**；任务内对该场景所有采纳测试点并发生成用例，**共享** `scene_rag_context`。
- 从阶段 A JSON schema **移除** `chunk_indexes`；`retrieve_chunks` 仅保留给其它流程，测试用例阶段 B 默认不用。
- `retrieval_trace` 键为 `scene_name`（非 `point_name`）。
- 在 PRD/提示词中强化：**测试点仅标题**；用例必须展开步骤与预期结果，且 `point_name` 与测试点一致。

**Non-Goals:**

- 阶段 A 从全库自动选文档（内网定制，开源不做）。
- 重跑文档解析流水线或新增 VLM 能力。
- 前端脑图/勾选交互变更。

## Decisions

1. **抽取 `build_case_rag_context(point, state)`**  
   - *理由*：集中多路召回与拼接，便于单测与 eval 记录 `retrieval_trace`。  
   - *位置*：`case_graph.py` 或 `case_generate/rag_context.py`。

2. **需求文档 collection**  
   - 统一使用 `requirement_collection_name()` → `requirement_docs`；种子文档与用户上传同库检索。  
   - 与 `test_case_docs_collection` 并列构成双路 RAG。

3. **检索模式**  
   - 阶段 B 默认 `hybrid`（复用 `KbRetrievalService` 已有 RRF）。  
   - `retrieve_chunks` 保留给其它流程，阶段 B 默认不用。

4. **测试点 / 用例语义**  
   - 阶段 A JSON：`test_points[].point_name` 为短标题，**不含** `chunk_indexes`。  
   - 阶段 B：先场景级 RAG，再 per-point 用例 LLM；用例 JSON 须含 `test_steps`、`expected_results`。

5. **为何不用 chunk_indexes**  
   - 索引由入库产生，LLM 难以稳定指对分片；同场景多点共享上下文，场景级 semantic/hybrid 召回更稳、更省调用。

## Risks / Trade-offs

- **[Risk] 需求文档种子未入库时通道为空** → 日志命中 0，不阻塞用例生成。  
- **[Risk] hybrid 延迟高于纯向量** → Semaphore 并发仍为 3；超时沿用 `RAGRetriever.timeout`。  
- **[Trade-off]** 开源 demo 无真实用例库时，Hit@3 仅在 eval 数据集预置 Qdrant fixture 或 mock 上验收。

## Open Questions

- （无）需求文档与用户上传已统一在 `requirement_docs`，靠 hybrid 语义召回相关片段。
