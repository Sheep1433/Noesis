## 1–5. 基线（已完成）

- [x] evals 包、runner、L0、dataset 9 条、`results/` gitignore

## 6. coverage（已完成，冻结）

- [x] `scorers/coverage.py`、`dataset golden_test_points`、pytest mock Judge
- [x] ~~6.5 扩至 20 条~~（取消：保留当前数据集规模，不再扩充）

## 7. rag（冻结于 mock 层）

- [x] `scorers/rag_hit.py`、pytest mock trace
- [x] ~~7.1 dataset `expected_rag` 标注~~（取消：不继续演进）
- [x] ~~8.4 Qdrant fixture + live baseline~~（取消：不继续演进）

## 8. 精简（已完成）

- [x] 移除 L1 关键词冒烟、L2 Judge、`EvalSettings`
- [x] 报告/CLI 仅 L0 + coverage + rag
- [x] 同步 README、`test_tdd_design.md`、OpenSpec

## 9. 归档

- [x] 9.1 合并至 `openspec/specs/test-case-agent-eval/spec.md`（冻结版）
