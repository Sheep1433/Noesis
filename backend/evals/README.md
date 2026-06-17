# 测试用例 Agent 离线评测

基于 [promptfoo](https://www.promptfoo.dev/)：**L0**、**coverage**、**rag** 三项指标。

## 目录

```
evals/
  dataset.py             # 数据集加载
  runner.py              # Agent 离线执行
  scoring.py             # 评分 + promptfoo 断言
  eval_targets.json      # 质量门阈值
  promptfoo/
    promptfooconfig.yaml
    provider.py          # promptfoo → runner
    tests.py             # dataset.jsonl → 测试用例
    run-python.sh
  datasets/test_case/
    dataset.jsonl
    documents/
```

## 跑分

```bash
cd backend

uv run python -m evals --tag baseline              # 全量，需 Qdrant + LLM
uv run python -m evals --tag cov --scope testpoints
uv run python -m evals --tag debug --item-id tc_login_001
uv run python -m evals --tag smoke --item-id tc_login_001 --mock-judge
```

环境变量：`NOESIS_EVAL_TAG`、`NOESIS_EVAL_SCOPE`、`NOESIS_EVAL_ITEM_ID`、`NOESIS_EVAL_LIMIT`、`NOESIS_EVAL_MOCK_JUDGE`。

## CI

`pytest tests/test_eval_*.py` 直接测 `scoring` / `runner`，不调 DashScope。
