## Purpose

本能力定义 Noesis **知识库检索离线评测**（`evals/kb/`）：对**单个 collection** 与 JSONL 基准集计算 Recall@K、Hit@K，支撑分块与检索参数调优。**与 `test-case-agent-eval` 互补**：本能力测通用 KB 检索链；后者测 TEST_CASE_QA 场景 RAG 端到端（两路 historical 通道、K=3）。

## Requirements

### Requirement: 与 test-case-agent-eval 的边界

| 维度 | `kb-evaluation`（本能力） | `test-case-agent-eval` 阶段 B |
|------|---------------------------|-------------------------------|
| 入口 | `uv run python -m evals.kb.run` | `uv run python -m evals.case --phase rag` |
| 范围 | 单 collection、通用 query | 场景级两路 RAG + Agent 流水线 |
| 金标准 | `relevant_chunk_ids` 或 file+header | `relevant_ids` + 场景 trace |
| 默认 K | `final_top_k` 或 `--k` | 固定 Recall@3 / Hit@3 |
| 断言复用 | MAY 与 `evals/case/shared/assertions.py` 共用 Recall/Hit 计算函数 | 已有 |

本能力 **SHALL NOT** 替代 `evals.case --phase rag`；两者 SHALL 共用 `KbRetrievalService` 与相同 `merge_query_execution_params` 语义。

#### Scenario: 评测与生产检索链一致

- **WHEN** 运行 `evals.kb.run` 且未传 `--query-params`
- **THEN** SHALL 读取目标集合 MySQL `query_params` 并调用 `KbRetrievalService`

### Requirement: 基准数据集格式

系统 SHALL 支持 JSONL，每行至少含 `query` 及 `relevant_chunk_ids`（字符串数组）或等价定位（`file_name` + `header_path` / `gold_snippet`）。

#### Scenario: 合法 JSONL 加载

- **WHEN** 提供符合格式的 JSONL
- **THEN** 评测入口 SHALL 成功加载全部有效行

#### Scenario: 缺少标注字段

- **WHEN** 某行缺少 `query` 或定位字段
- **THEN** 系统 SHALL 跳过该行并记录 warning，不中断整次评测

### Requirement: 评测执行入口

系统 SHALL 提供 `uv run python -m evals.kb.run`，接受 `--dataset`、`--collection`、可选 `--query-params` JSON、可选 `--k`。

#### Scenario: 对单集合跑通评测

- **WHEN** 集合存在且基准集非空
- **THEN** 命令 SHALL 输出聚合指标与失败样本列表

### Requirement: 检索指标

系统 SHALL 计算 **Recall@K** 与 **Hit@K**（K 默认取有效 `final_top_k`，CLI `--k` 可覆盖）。

- Hit@K：top-k 中是否出现任一 `relevant_chunk_ids`
- Recall@K：`|relevant ∩ topK| / |relevant|`（逐样本平均）

#### Scenario: 全命中样本

- **WHEN** 某 query 的全部 `relevant_chunk_ids` 出现在 top-k
- **THEN** 该样本 Recall@K SHALL 为 1.0

#### Scenario: 无相关标注

- **WHEN** 某行 `relevant_chunk_ids` 为空
- **THEN** 系统 SHALL 跳过该样本的 Recall 计算

### Requirement: 评测报告

评测结束 SHALL 输出 JSON 摘要（`recall_at_k`、`hit_at_k`、样本数、失败列表）及控制台人类可读摘要。

#### Scenario: 报告含聚合指标

- **WHEN** 评测成功完成
- **THEN** JSON SHALL 含 `recall_at_k` 与 `hit_at_k` 数值字段

### Requirement: 可选 LLM Judge

系统 MAY 支持 `--judge` 对 top 片段与 `gold_answer` 做二元判定；缺省 **SHALL NOT** 调用 LLM。

#### Scenario: 未启用 judge

- **WHEN** 未传入 `--judge`
- **THEN** 评测 SHALL 仅输出检索指标

### Requirement: 固定 fixture 样例

系统 SHALL 提供 `evals/kb/fixtures/sample.jsonl` 与 DeepDoc/markdown 降级路径冒烟样例。

#### Scenario: CI 冒烟

- **WHEN** 对测试 collection 运行 fixtures 评测
- **THEN** SHALL 产出非空 JSON 报告且 exit code 为 0
