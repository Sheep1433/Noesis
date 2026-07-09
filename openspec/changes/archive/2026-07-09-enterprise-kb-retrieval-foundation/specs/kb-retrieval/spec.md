## Purpose

本能力定义 Noesis **知识库检索模块**（`kb/retrieval`）行为：单一门面 `KbRetrievalService` 提供 hybrid 默认、recall → rerank → `score_threshold` → `final_top_k` 链，集合级 `query_params` 从 MySQL 读取并与请求覆盖合并。供 HTTP API、COMMON_QA Agent、测试用例场景 RAG 与 `kb-evaluation` 复用。BM25 首版为进程内索引（单实例假设，见 design.md）。

## Requirements

### Requirement: 统一检索门面

系统 SHALL 提供 `KbRetrievalService.search()`，接受 `collection_name`、`query`、合并后的 `query_execution_params`、可选 `filters`，返回 `KbSearchHit` 列表。每条命中 SHALL 含 `id`、`content`、`file_name`、`search_mode`、`score`（最终排序分）；SHALL 含 `recall_score`；启用 rerank 时 SHALL 含 `rerank_score`（若可用）。

#### Scenario: 门面检索成功

- **WHEN** 集合存在、Qdrant 已连接、查询非空
- **THEN** 系统 SHALL 返回按最终 `score` 降序排列的命中列表

#### Scenario: 集合不存在

- **WHEN** `collection_name` 在 Qdrant 中不存在
- **THEN** 系统 SHALL 返回 HTTP 404 及统一失败结构，且 SHALL NOT 以空列表冒充成功

### Requirement: 检索参数字段与合并

`query_execution_params` canonical 字段 SHALL 为：`search_mode`、`use_reranker`、`recall_top_k`、`final_top_k`、`score_threshold`、`rrf_k`。

- `limit` SHALL 作为 **deprecated 别名**：读入时映射为 `final_top_k`；与 `final_top_k` 同时存在时 **`final_top_k` 优先**。
- 合并顺序：**MySQL 集合 `query_params`** → **平台默认** → **单次请求覆盖**（非 null 字段）。
- 平台默认：`search_mode=hybrid`、`use_reranker=true`、`recall_top_k=50`、`final_top_k=10`、`score_threshold=null`、`rrf_k=60`。

#### Scenario: limit 别名归一化

- **WHEN** 合并输入仅含 `limit=12`
- **THEN** 有效 `final_top_k` SHALL 为 12

#### Scenario: 集合级默认覆盖平台默认

- **WHEN** MySQL 中 `final_top_k=15` 且请求未覆盖
- **THEN** 有效 `final_top_k` SHALL 为 15

### Requirement: 默认 hybrid 检索

当 `search_mode` 未指定时，系统 SHALL 使用 `hybrid`。`hybrid` SHALL 使用 RRF 融合向量与 BM25，`rrf_k` 默认 60。

#### Scenario: 未传 search_mode 为 hybrid

- **WHEN** 合并后的参数未包含 `search_mode`
- **THEN** 实际执行模式 SHALL 为 `hybrid`

#### Scenario: 显式 vector 仍可用

- **WHEN** 调用方指定 `search_mode=vector`
- **THEN** 系统 SHALL 仅执行向量检索

### Requirement: 检索流水线顺序

检索 SHALL 按以下顺序执行：

1. 在当前 `search_mode` 下召回至多 `recall_top_k` 条候选（含 filters）
2. 若 `use_reranker=true` 且 rerank 可用，对候选 cross-encoder 重排
3. 若 `score_threshold` 非 null，**在 rerank 之后**（无 rerank 则在 recall 分上）过滤低于阈值的命中
4. 按最终分降序截断为至多 `final_top_k` 条

最终 `score` SHALL 为：有 rerank 时用 `rerank_score`；否则用 recall 融合分。

#### Scenario: rerank 后应用 score_threshold

- **WHEN** `use_reranker=true`、`score_threshold=0.5`，且某候选 recall 分高但 rerank 分低于 0.5
- **THEN** 该候选 SHALL NOT 出现在最终结果中

#### Scenario: 启用 rerank 时先扩召回

- **WHEN** `use_reranker=true`、`recall_top_k=50`、`final_top_k=10`
- **THEN** 召回阶段 SHALL 保留至多 50 条候选供 rerank
- **AND** 最终返回 SHALL 不超过 10 条

### Requirement: cross-encoder rerank

当 `use_reranker=true` 且平台 rerank 模型已配置（`config.yaml` + API Key）时，系统 SHALL 对 recall 候选调用 rerank。未配置或调用失败时 SHALL 降级为 recall 分排序，记录 warning，**SHALL NOT** 使检索失败。

#### Scenario: rerank 成功改变顺序

- **WHEN** rerank 可用且候选数大于 `final_top_k`
- **THEN** 返回顺序 SHALL 以 `rerank_score` 降序为准

#### Scenario: rerank 不可用降级

- **WHEN** `use_reranker=true` 但 rerank API Key 为空
- **THEN** 系统 SHALL 跳过 rerank 并按 recall 分数返回

### Requirement: BM25 中文分词

`search_mode=bm25` 或 `hybrid` 时，BM25 SHALL 对中文查询使用 jieba `cut_for_search` 预处理。

#### Scenario: 中文关键词命中

- **WHEN** 文档含「登录页」且查询为「登录」
- **THEN** BM25 或 hybrid 路径 SHALL 有机会召回该分片

### Requirement: 元数据过滤

检索门面 SHALL 接受可选 `filters`：`file_name` / `source_name` 精确匹配；`Header_1`~`Header_4` 精确匹配（AND）；`header_path_prefix` 前缀匹配。过滤 SHALL 在 recall 阶段应用，并与 rerank 组合。

#### Scenario: 按 file_name 过滤

- **WHEN** `filters={"file_name": "spec.md"}` 且集合内存在该文件分片
- **THEN** 返回结果 SHALL 仅含该文件分片

### Requirement: Agent 与 API 语义一致

对相同 `collection_name`、`query`、合并后 `query_execution_params` 与 `filters`（且无并发写入），HTTP `POST .../search` 与直接调用 `KbRetrievalService.search` SHALL 返回一致的 top-k 集合（`file_name`+`content` 主键相同；分数允许浮点微差）。

#### Scenario: API 与门面结果一致

- **WHEN** 同一参数分别经 API 与单元测试直接调门面
- **THEN** 返回条数与主键集合 SHALL 一致

### Requirement: Qdrant 不可用

当 Qdrant 未连接且 `search_mode` 为 `vector` 或 `hybrid` 时，系统 SHALL 返回 HTTP 503 及明确错误信息。

#### Scenario: 向量库断开

- **WHEN** `is_qdrant_connected()` 为 false
- **THEN** 检索 SHALL 返回 503

### Requirement: 缓存失效

文档入库、删除或集合清空后，系统 SHALL 调用 `KbRetrievalService.invalidate_cache(collection_name)`。

#### Scenario: 上传后新分片可检索

- **WHEN** 向集合上传新文档并成功索引
- **THEN** 随后检索 SHALL 可命中新分片，无需重启进程

### Requirement: 禁止绕过门面

COMMON_QA、测试用例场景 RAG、HTTP API **SHALL NOT** 直接调用底层 `Retrieval` 类或 Qdrant 检索；一律经 `KbRetrievalService`。

#### Scenario: Agent 工具经门面

- **WHEN** `search_knowledge_bases_all` 执行检索
- **THEN** SHALL 调用 `KbRetrievalService.search`，而非 `QdrantService` 向量查询
