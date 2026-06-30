## Purpose

本能力定义 Noesis **知识库检索离线评测** 行为：对指定集合与基准问答集计算 Recall@K、Hit@K 等指标，支撑分块与检索参数调优回归，并与测试用例 Agent 评测中的 RAG 命中指标语义对齐。

## ADDED Requirements

### Requirement: 基准数据集格式

系统 SHALL 支持 JSONL 基准文件，每行至少含：`query`（字符串）及 `relevant_chunk_ids`（字符串数组）或等价定位字段（`file_name` + `header_path` / `gold_snippet`）。

#### Scenario: 合法 JSONL 加载

- **WHEN** 提供符合格式的 JSONL 路径
- **THEN** 评测入口 SHALL 成功加载全部样本行

#### Scenario: 缺少标注字段

- **WHEN** 某行缺少 `query` 或任一相关定位字段
- **THEN** 系统 SHALL 跳过该行并记录 warning，且 SHALL NOT 中断整次评测

### Requirement: 评测执行入口

系统 SHALL 提供命令行入口（例如 `uv run python -m evals.kb.run`），接受 `--dataset`、`--collection`、可选 `--query-params` JSON 覆盖，并对每条 `query` 调用 `KbRetrievalService` 执行与生产一致的检索链。

#### Scenario: 对单集合跑通评测

- **WHEN** 集合存在且基准集非空
- **THEN** 命令 SHALL 输出每条样本的命中情况汇总及聚合指标

### Requirement: 检索指标

系统 SHALL 至少计算 **Recall@K** 与 **Hit@K**（K 取自 `final_top_k` 或 CLI `--k`）。Hit@K 定义为 top-k 结果中是否出现任一 `relevant_chunk_ids`；Recall@K 定义为相关 id 被召回比例（多样本平均）。

#### Scenario: 全命中样本

- **WHEN** 某 query 的 `relevant_chunk_ids` 全部出现在 top-k
- **THEN** 该样本 Recall@K SHALL 为 1.0

#### Scenario: 无相关标注

- **WHEN** 某行 `relevant_chunk_ids` 为空
- **THEN** 系统 SHALL 跳过该样本的 Recall 计算

### Requirement: 评测报告

评测结束 SHALL 输出机器可读 JSON 摘要（总样本数、平均 Recall@K、Hit@K、失败样本列表）及人类可读控制台摘要。

#### Scenario: 报告含聚合指标

- **WHEN** 评测成功完成
- **THEN** JSON 输出 SHALL 含 `recall_at_k` 与 `hit_at_k` 数值字段

### Requirement: 与生产检索参数一致

评测 SHALL 默认读取目标集合 MySQL `query_params`；CLI 传入的 `--query-params` SHALL 按与 HTTP 相同的合并语义覆盖持久化默认。

#### Scenario: 评测使用集合 rerank 配置

- **WHEN** 集合配置 `use_reranker=true`
- **THEN** 评测检索链 SHALL 启用 rerank（在 rerank 可用前提下）

### Requirement: 可选 LLM Judge

系统 MAY 支持 `--judge` 对 top 文档片段与 `gold_answer` 做二元正确性判定；该路径为可选，缺省评测 SHALL 仅依赖检索指标。

#### Scenario: 未启用 judge

- **WHEN** 未传入 `--judge`
- **THEN** 评测 SHALL 不调用 LLM，仅输出检索指标
