## Context

- **简历对齐**：测试点覆盖准确率、RAG Hit@3 ≈ 85%、回归集 ≥20 条（当前 9 条）。
- **生成流水线**：测试场景+测试点 → 用户勾选（线上）→ 用例生成（场景级 RAG）。评测不复用阶段 A/B 编号。
- **评测输入**：`document_path` + `scenario_description`（= Agent `query`）。
- **前置**：`cases`/`full` 需 Qdrant 已连接；coverage 需 LLM（live）或 mock（CI）。

## 评测指标（仅此三项）

| 指标 | 代码 | 含义 | scope |
|------|------|------|-------|
| **L0** | `scorers/l0_structure.py` | 无 `error`、schema 合法 | 全部 |
| **coverage** | `scorers/coverage.py` | LLM Judge → `point_coverage_recall` | `testpoints` / `full` |
| **rag** | `scorers/rag_hit.py` | `retrieval_trace` vs `expected_rag` → `rag_hit_at_3` | `cases` / `full` |

质量门：`passed` = L0 通过且 coverage ≥ `eval_targets.json` 的 `point_coverage_recall_min`。RAG 阈值 `rag_hit_at_3_min` 作用于全量汇总（`rag_threshold_failed`）。

## Dataset Schema

```json
{
  "id": "tc_login_001",
  "scenario_description": "重点覆盖登录失败与验证码",
  "document_path": "documents/tc_login_001.md",
  "ground_truth": {
    "golden_test_points": [
      { "scene_name": "用户登录", "point_name": "用户名密码错误提示" }
    ],
    "expected_rag": {
      "用户登录": {
        "current_requirement": { "expected_ids": ["<qdrant-point-id>"] },
        "historical_requirements": { "expected_ids": ["<qdrant-point-id>"] },
        "historical_test_cases": { "expected_ids": ["<qdrant-point-id>"] }
      }
    }
  }
}
```

## 目录结构

```
backend/evals/
  scorers/l0_structure.py
  scorers/coverage.py
  scorers/rag_hit.py
  runners/test_case.py
  eval_targets.json
  __main__.py
```

## 待办

- 回归集扩至 20 条；补 `expected_rag` 金标准与 Qdrant fixture（live RAG 验收）。
