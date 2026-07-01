# test-case-agent-eval Specification

## Purpose

本能力规定 Noesis **测试用例 Agent 离线评测**（`backend/evals/case/`）的验收标准：两阶段 promptfoo 流水线（阶段 A 测试点、阶段 B RAG 检索）、CLI 入口、目录布局、Judge 策略，以及与 `evals.agent`、`evals.compression` 的隔离边界。线上 `TEST_CASE_QA` 流水线行为见 `agent-test-case`。

## Requirements

### Requirement: 测试用例评测入口为 evals.case 子模块

测试用例 Agent 离线评测 SHALL 通过 `uv run python -m evals.case` 启动，并 **SHALL** 使用 `--phase testpoints` 或 `--phase rag`（别名 `stage-a` / `stage-b`）选择阶段。

CLI **SHALL** 支持 `--tag`（必填）、`--item-id`、`--limit`、`--baseline`（promptfoo `--compare`）、`--output`；行为以 `backend/evals/case/__main__.py` 为准。

`uv run python -m evals` **SHALL NOT** 直接执行 promptfoo 评测，仅 SHALL 输出各场景子模块说明。

#### Scenario: 根包不跑分

- **WHEN** 开发者运行 `uv run python -m evals`
- **THEN** 系统 SHALL 打印 `evals.case` / `evals.agent` / `evals.compression` 用法提示，**SHALL NOT** 调用 promptfoo

#### Scenario: 测试点阶段评测

- **WHEN** 开发者运行 `uv run python -m evals.case --phase testpoints --tag baseline`
- **THEN** 系统 SHALL 执行 `evals/case/testpoints/promptfooconfig.yaml` 定义的 promptfoo 流水线

#### Scenario: RAG 阶段评测

- **WHEN** 开发者运行 `uv run python -m evals.case --phase rag --tag rb-baseline`
- **THEN** 系统 SHALL 执行 `evals/case/rag/promptfooconfig.yaml` 定义的 promptfoo 流水线

### Requirement: 测试用例评测代码与数据集布局

评测实现 **SHALL** 位于 `backend/evals/case/`，至少包含：

| 路径 | 职责 |
|------|------|
| `testpoints/golden/` | 阶段 A 金标准源（`prd_*.yaml`） |
| `testpoints/golden_loader.py`、`generate_eval_dataset.py` | 读取 golden、生成 PRD 与 promptfooconfig |
| `testpoints/promptfooconfig.yaml` | 阶段 A 运行时配置（`golden_test_points_json` 由脚本生成） |
| `testpoints/documents/` | 输入需求 PRD |
| `report.py` | 跑分后解析 promptfoo JSON、打印/写入 summary |
| `results/<tag>/` | 默认 `--output` 与 `*-summary.json` |
| `rag/promptfooconfig.yaml` | 阶段 B RAG 评测集与 `relevant_ids` 金标准 |
| `rag/corpus/test_cases/` | RAG 灌库历史用例语料 |
| `rag/ingest.py` | Qdrant 灌库 |
| `shared/assertions.py`、`shared/judge.py` | 断言与 LLM Judge |

系统 **SHALL NOT** 要求单独的 `dataset.jsonl` 或已废弃的 `evals/case/datasets/test_case/` 路径。

#### Scenario: 评测集在 promptfooconfig

- **WHEN** 审查默认评测数据
- **THEN** 阶段 A 金标准源 **SHALL** 位于 `testpoints/golden/*.yaml`；运行时 **SHALL** 由生成脚本写入 `promptfooconfig.yaml` 的 `tests[].vars.golden_test_points_json`

### Requirement: 阶段 A 指标

阶段 A（`testpoints`）SHALL 至少汇报：L0 结构门禁、`point_coverage_recall`、`point_coverage_precision`（以 `shared/assertions.py` 与 promptfoo rubric 为准）。

#### Scenario: L0 结构失败

- **WHEN** 产出 JSON 缺少必选字段或含 `error` 键
- **THEN** L0 断言 SHALL 标记该项不通过

### Requirement: 阶段 B RAG 指标

阶段 B（`rag`）SHALL 在灌库后评测两路 RAG 的 Recall@3 / Hit@3 及 `document_context_present`（以 `rag/promptfooconfig.yaml` 与 assertions 为准）。

#### Scenario: 灌库与评测分离

- **WHEN** 开发者需重建 RAG 向量索引
- **THEN** SHALL 使用 `uv run python -m evals.case.rag.ingest`（`--map-only` 或 `--reset`）后再跑 `--phase rag`

### Requirement: 测试用例评测无仓库级质量门与无 mock Judge 开关

promptfoo 评测 **SHALL NOT** 依赖 `eval_targets.json`、`--mock-judge` 或 `NOESIS_EVAL_MOCK_JUDGE`。

coverage 类 rubric **SHALL** 经 `shared/judge.py` 调用 `get_llm()` 执行真实 Judge。

pytest 回归 **MAY** 对 Judge 使用 mock 以隔离 DashScope；**SHALL NOT** 与上述 CLI 评测 mock 开关混用。

#### Scenario: coverage 无阈值门禁

- **WHEN** promptfoo 计算 `point_coverage_recall=0.42`
- **THEN** 断言 SHALL 记录该分数，**SHALL NOT** 因仓库级阈值自动失败

#### Scenario: 手动跑分使用真实 LLM

- **WHEN** 运行 `uv run python -m evals.case --phase testpoints --tag manual`
- **THEN** coverage Judge SHALL 调用 `get_llm()`，**SHALL NOT** 使用名称匹配 mock

### Requirement: 与 Agent 评测目录隔离

`evals.case` **SHALL NOT** 与 `evals/agent/` 或 `evals/compression/` 共用 runner 或结果目录。

#### Scenario: case 不启动 DeepResearchAgent 全链路 benchmark

- **WHEN** 开发者运行 `uv run python -m evals.case`
- **THEN** 系统 SHALL 直调 `case_graph` 或 promptfoo provider，**SHALL NOT** 调用 `evals.agent.browsecomp`
