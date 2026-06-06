## Why

测试用例生成 Agent 需要可重复离线评测，围绕三项指标：**L0 结构门禁**、**测试点覆盖准确率**（`point_coverage_recall`）、**RAG Hit@3**（`rag_hit_at_3`）。runner 用 `--scope testpoints|cases|full` 控制执行节点。

## What Changes

- 评测仅保留 **L0 + coverage + rag**（移除 L1 关键词冒烟、L2 可选 Judge）。
- 数据集：`scenario_description`、`golden_test_points`、可选 `expected_rag`。
- runner 全量采纳测试点；报告 `aggregate` 汇总三项指标。

## Capabilities

- `test-case-agent-eval`：离线评测（L0 / coverage / rag）。

## Impact

- `backend/evals/` scorers、runner、dataset、README
- `docs/test/test_tdd_design.md`
- 非 BREAKING：评测为增量
