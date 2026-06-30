## Purpose

本能力定义 Noesis **知识库检索模块**（`kb/retrieval`）的最终行为：在单一门面下提供 hybrid 默认、recall 与 rerank 两阶段检索、集合级 `query_params` 持久化读取，供 HTTP API、COMMON_QA Agent 与测试用例场景 RAG 复用。存储以 Qdrant 为主；BM25 首版为进程内索引。

## ADDED Requirements

### Requirement: 统一检索门面

系统 SHALL 提供 `KbRetrievalService`（或等价门面），接受 `collection_name`、`query`、`query_execution_params`（合并后），返回统一结构命中列表。每条命中 SHALL 含 `id`、`content`、`file_name`、`search_mode`、融合分 `score`；启用 rerank 时 SHALL 含 `rerank_score`（若可用）。

#### Scenario: 门面检索成功

- **WHEN** 集合存在、Qdrant 已连接、查询非空
- **THEN** 系统 SHALL 返回按最终分数降序排列的命中列表

#### Scenario: 集合不存在

- **WHEN** `collection_name` 在 Qdrant 中不存在
- **THEN** 系统 SHALL 返回 HTTP 404（或业务码一致）及统一失败结构，且 SHALL NOT 以空列表冒充成功

### Requirement: 默认 hybrid 检索

当 `query_execution_params.search_mode` 未指定时，系统 SHALL 使用 `hybrid`。`hybrid` SHALL 使用 RRF 融合向量与 BM25 两路排序，`rrf_k` 默认 60，支持请求级覆盖。

#### Scenario: 未传 search_mode 为 hybrid

- **WHEN** 合并后的参数未包含 `search_mode`
- **THEN** 实际执行模式 SHALL 为 `hybrid`

#### Scenario: 显式 vector 仍可用

- **WHEN** 调用方指定 `search_mode=vector`
- **THEN** 系统 SHALL 仅执行向量检索，行为与参数一致

### Requirement: recall 与 final_top_k 两阶段

系统 SHALL 支持 `recall_top_k`（默认 50）与 `final_top_k`（默认 10）。检索 SHALL 先在当前 `search_mode` 下召回至多 `recall_top_k` 条候选，再进入 rerank（若启用）并截断为 `final_top_k`。

#### Scenario: 启用 rerank 时先扩召回

- **WHEN** `use_reranker=true` 且 `recall_top_k=50`、`final_top_k=10`
- **THEN** hybrid 召回阶段 SHALL 保留至多 50 条候选供 rerank
- **AND** 最终返回 SHALL 不超过 10 条

### Requirement: cross-encoder rerank

当 `use_reranker=true` 且平台 rerank 模型已配置（`config.yaml` + API Key）时，系统 SHALL 对 recall 候选调用 rerank 模型重排。当 rerank 未配置或调用失败时，系统 SHALL 降级为按 recall 分数排序，并 SHALL 记录 warning，且 SHALL NOT 使检索请求失败。

#### Scenario: rerank 成功改变顺序

- **WHEN** rerank 可用且候选数大于 `final_top_k`
- **THEN** 返回顺序 SHALL 以 `rerank_score` 降序为准（同分可保持 recall 次序）

#### Scenario: rerank 不可用降级

- **WHEN** `use_reranker=true` 但 rerank API Key 为空
- **THEN** 系统 SHALL 跳过 rerank 并按 hybrid/vector 分数返回结果

### Requirement: 集合 query_params 持久化读取

检索门面 SHALL 从 MySQL `kb_collection_config.query_params` 读取集合默认，再与 HTTP/Agent 单次请求覆盖合并；显式请求字段 SHALL 优先于持久化默认。

#### Scenario: 集合级默认 final_top_k

- **WHEN** 集合配置 `final_top_k=15` 且请求未传 `limit`/`final_top_k`
- **THEN** 实际返回上限 SHALL 为 15

### Requirement: BM25 中文分词

`search_mode=bm25` 或 `hybrid` 时，BM25 SHALL 对中文查询使用 jieba `cut_for_search` 预处理（与现有 `kb_bm25_preprocess` 行为一致）。

#### Scenario: 中文关键词命中

- **WHEN** 文档含「登录页」且查询为「登录」
- **THEN** BM25 或 hybrid 路径 SHALL 有机会召回该分片

### Requirement: 元数据过滤

检索门面 SHALL 接受可选 `filters`，支持：`file_name` / `source_name` 精确匹配；`Header_1`~`Header_4` 精确匹配（AND）；`header_path_prefix` 前缀匹配。过滤 SHALL 与任意 `search_mode` 及 rerank 组合使用。

#### Scenario: 按 file_name 过滤

- **WHEN** `filters={"file_name": "spec.md"}` 且集合内存在该文件分片
- **THEN** 返回结果 SHALL 仅含该文件分片

### Requirement: Agent 与 API 语义一致

对相同 `collection_name`、`query` 与合并后 `query_execution_params`（且并发无写入），经 HTTP `POST .../search` 与经 `KbRetrievalService` 直接调用 SHALL 返回语义一致的 top-k（允许分数浮点微差）。

#### Scenario: API 与门面结果一致

- **WHEN** 同一参数分别经 API 与单元测试直接调门面
- **THEN** 返回条数与 `file_name`+`content` 主键集合 SHALL 一致

### Requirement: Qdrant 不可用

当 Qdrant 未连接且调用方请求 `vector` 或 `hybrid` 时，系统 SHALL 返回 HTTP 503 及明确错误信息。

#### Scenario: 向量库断开

- **WHEN** `is_qdrant_connected()` 为 false
- **THEN** 检索 API SHALL 返回 503

### Requirement: 缓存失效

文档入库、删除或集合清空后，系统 SHALL 调用 `KbRetrievalService.invalidate_cache(collection_name)`，确保 BM25 索引与向量侧一致。

#### Scenario: 上传后新分片可检索

- **WHEN** 向集合上传新文档并成功索引
- **THEN** 随后检索 SHALL 可命中新分片，无需重启进程
