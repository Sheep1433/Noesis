# 测试用例 Agent 离线评测

三层指标：**L0**（结构门禁）、**coverage**（测试点覆盖）、**rag**（RAG Hit@3）。

## 目录

```
evals/
  datasets/test_case/dataset.jsonl
  datasets/test_case/documents/
  eval_targets.json
  runners/
  scorers/          # l0_structure.py, coverage.py, rag_hit.py
  report.py
```

产物：`results/<run_id>/`（已 gitignore）。

## Dataset

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | 唯一标识 |
| `scenario_description` | 是 | 写入 Agent `query`（兼容 `query`） |
| `document_path` | 是 | 相对 `datasets/test_case/` 的 Markdown |
| `ground_truth.golden_test_points` | 是 | `[{scene_name, point_name}]` |
| `ground_truth.expected_rag` | 可选 | 评 RAG 时必填，channel：`current_requirement` / `historical_requirements` / `historical_test_cases` |

## 跑分

```bash
cd backend
uv run python -m evals --tag baseline              # scope=full，需 Qdrant + LLM
uv run python -m evals --tag cov --scope testpoints
uv run python -m evals --tag debug --item-id tc_login_001
uv run python -m evals --tag v2 --baseline ../results/<run_id>
```

## 指标

| 指标 | 含义 | 何时计算 |
|------|------|----------|
| **L0** | 无 error、JSON 字段齐全 | 始终 |
| **coverage** | `point_coverage_recall`（LLM Judge） | `testpoints` / `full` |
| **rag** | `rag_hit_at_3` | `cases` / `full`，需 `expected_rag` |

汇总：`aggregate.json` → `l0_pass_rate`、`coverage.*`、`rag.*`、`dataset_size_warning`。

质量门：`passed` = L0 通过且 coverage ≥ `eval_targets.json` 中的 `point_coverage_recall_min`。

## CI

`pytest tests/test_eval_*.py` 使用 mock Judge / mock trace，不调 DashScope。
