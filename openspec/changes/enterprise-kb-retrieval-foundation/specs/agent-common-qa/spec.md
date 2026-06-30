## MODIFIED Requirements

### Requirement: search_knowledge_base SHALL 跨全部 Collection 执行 hybrid 检索

工具实现 `search_knowledge_bases_all` SHALL：

1. 枚举当前可检索的全部 Collection 名称（`list_qdrant_collection_names`）。
2. 对每个 Collection 从 MySQL 读取 `query_params`，再与工具入参 `limit` 合并：`final_top_k = min(limit, 合并后 final_top_k)`；`search_mode` 默认取集合配置（缺省 `hybrid`）；`use_reranker` / `recall_top_k` 取集合配置（缺省启用 rerank 与 `recall_top_k=50`）。
3. 对每个 Collection 调用 `KbRetrievalService.search`（完整两阶段检索链），单库召回上限为 `per_collection = max(recall_top_k 合并值, max(3, ceil(final_top_k / collection_count)))` 的合理实现（以保证全局 merge 后有足够候选，具体以实现文档为准，但 SHALL NOT 仅传 `final_top_k` 而不经 recall 扩窗）。
4. 合并各库命中后按最终 `score`（优先 `rerank_score`，否则 recall 分）降序排序，取全局 `final_top_k`。
5. 单库检索异常 SHALL 记录 warning 并跳过该 Collection，不得导致整次工具调用崩溃。

#### Scenario: 多库合并取 Top-K

- **WHEN** 存在 Collection A、B 且工具以 `limit=10` 被调用
- **THEN** 系统 SHALL 分别对 A、B 执行含 hybrid 与 rerank（可用时）的检索
- **AND** 返回结果 SHALL 为全局 score 最高的至多 10 条命中

#### Scenario: 单库失败不阻断其它库

- **WHEN** 对某一 Collection 的检索抛出异常
- **THEN** 系统 SHALL 跳过该 Collection 并继续检索其余 Collection
- **AND** 若其它库有命中，工具 SHALL 仍返回合并后的 Top-K

### Requirement: 工具输出 SHALL 为结构化 JSON 字符串

`search_knowledge_base` 的返回值 SHALL 为 UTF-8 JSON 字符串（`ensure_ascii=False`），语义如下：

| 场景 | 载荷 |
|------|------|
| 向量库未连接 | `{"error": "向量库未连接，无法检索"}` |
| 无可用 Collection | `{"hits": [], "message": "当前无可用知识库 Collection"}` |
| 无命中 | `{"hits": [], "message": "未检索到相关片段"}` |
| 有命中 | `{"hits": [{rank, collection_name, file_name, score, search_mode, header_path, content, rerank_score?}, ...]}` |

每条 hit 的 `score` SHALL 为四舍五入至 4 位小数的浮点数；`rank` 从 1 递增。若启用 rerank，`score` SHALL 反映最终排序分（优先 rerank）。

#### Scenario: 有命中时的字段完整性

- **WHEN** 检索返回至少一条命中
- **THEN** JSON `hits` 数组每项 SHALL 含 `collection_name`、`file_name`、`content` 与 `score`
- **AND** Agent 可据 `collection_name` 与 `file_name` 在回答中标注来源

#### Scenario: 无命中时的明确语义

- **WHEN** 全部 Collection 检索后无满足阈值的片段
- **THEN** 工具 SHALL 返回 `hits: []` 与 `message: "未检索到相关片段"`
- **AND** Agent 依系统提示词 SHALL 向用户说明知识库未覆盖该问题
