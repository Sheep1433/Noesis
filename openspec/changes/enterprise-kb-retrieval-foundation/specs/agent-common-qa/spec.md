## MODIFIED Requirements

### Requirement: search_knowledge_base SHALL 跨全部 Collection 执行检索

工具实现 `search_knowledge_bases_all` SHALL：

1. 枚举可检索 Collection（`list_qdrant_collection_names`）。
2. 计算全局 `final_top_k = min(tool.limit, 20)`（工具入参 `limit` 默认 10，范围 1–20）。
3. 对每个 Collection **独立**：
   - 从 MySQL 读取该库 `query_params`，与 `{}` 合并（**不得**用全局常量替代 per-collection 配置）
   - 将全局 `final_top_k` 作为该次调用的请求覆盖：`request_overrides = {"final_top_k": global_final_top_k}`
   - 调用 `KbRetrievalService.search`（完整 recall → rerank → threshold → truncate 链，见 `kb-retrieval`）
4. 合并各库命中：按最终 `score`（rerank 分优先，否则 recall 分）降序，取全局 Top `global_final_top_k`。
5. 单库异常 SHALL 记录 warning 并跳过，不得导致整次工具崩溃。

**跨库 per-collection 说明**：每库 `KbRetrievalService.search` 内部已按该库 `recall_top_k` 扩召回；工具层 **SHALL NOT** 再手动截断为 `ceil(global_final_top_k / N)` 以免丢失 rerank 候选。全局 merge 仅在各库返回结果之后执行。

#### Scenario: 多库合并取 Top-K

- **WHEN** 存在 Collection A、B 且工具以 `limit=10` 被调用
- **THEN** 系统 SHALL 分别对 A、B 经 `KbRetrievalService` 检索（含 hybrid 与 rerank，可用时）
- **AND** 返回全局 score 最高的至多 10 条

#### Scenario: 各库独立 query_params

- **WHEN** A 库 `use_reranker=false`、B 库 `use_reranker=true` 且 rerank 可用
- **THEN** A 库结果 SHALL 无 rerank 重排，B 库结果 SHALL 经 rerank

#### Scenario: 单库失败不阻断其它库

- **WHEN** 对某一 Collection 检索抛出异常
- **THEN** 系统 SHALL 跳过该库并继续
- **AND** 若其它库有命中，工具 SHALL 仍返回合并 Top-K

### Requirement: 工具输出 SHALL 为结构化 JSON 字符串

`search_knowledge_base` 返回值 SHALL 为 UTF-8 JSON 字符串（`ensure_ascii=False`）：

| 场景 | 载荷 |
|------|------|
| 向量库未连接 | `{"error": "向量库未连接，无法检索"}` |
| 无可用 Collection | `{"hits": [], "message": "当前无可用知识库 Collection"}` |
| 无命中 | `{"hits": [], "message": "未检索到相关片段"}` |
| 有命中 | `{"hits": [{rank, collection_name, file_name, score, search_mode, header_path, content, rerank_score?, recall_score?}, ...]}` |

`score` SHALL 四舍五入至 4 位小数；启用 rerank 时 `score` SHALL 反映最终排序分。

#### Scenario: 有命中时的字段完整性

- **WHEN** 检索返回至少一条命中
- **THEN** 每项 SHALL 含 `collection_name`、`file_name`、`content`、`score`

#### Scenario: 无命中时的明确语义

- **WHEN** 全部 Collection 检索后无满足阈值的片段
- **THEN** 工具 SHALL 返回 `hits: []` 与 `message: "未检索到相关片段"`
