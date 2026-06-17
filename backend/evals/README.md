# 测试用例 Agent 离线评测

基于 [promptfoo](https://www.promptfoo.dev/) 编排：**L0**（结构门禁）、**coverage**（测试点覆盖）、**rag**（RAG Hit@3）。

## 目录

```
evals/
  promptfoo/
    promptfooconfig.yaml   # promptfoo 主配置
    provider.py            # 调用 case_graph 离线 runner
    tests.py               # 从 dataset.jsonl 生成测试用例
    asserts/               # L0 / coverage / rag Python 断言
  datasets/test_case/dataset.jsonl
  datasets/test_case/documents/
  eval_targets.json
  scorers/                 # 评分逻辑（断言复用）
  runners/                 # Agent 离线 runner（provider 复用）
```

产物：promptfoo 默认写入 `backend/evals/promptfoo/.promptfoo/`；可用 `-o` 导出 JSON。

## 前置

- Node.js（`npx`）
- `backend/.env` 配置 LLM；`scope=cases|full` 时需 Qdrant

## Dataset

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | 唯一标识 |
| `scenario_description` | 是 | 写入 Agent `query`（兼容 `query`） |
| `document_path` | 是 | 相对 `datasets/test_case/` 的 Markdown |
| `ground_truth.golden_test_points` | 是 | `[{scene_name, point_name}]` |
| `ground_truth.expected_rag` | 可选 | 评 RAG 时必填 |

## 跑分

```bash
cd backend

# 全量（需 Qdrant + LLM）
uv run python -m evals --tag baseline

# 仅测测试点生成
uv run python -m evals --tag cov --scope testpoints

# 单条调试
uv run python -m evals --tag debug --item-id tc_login_001

# mock Judge（不调 DashScope，适合本地冒烟）
uv run python -m evals --tag smoke --item-id tc_login_001 --mock-judge

# 与历史结果对比
uv run python -m evals --tag v2 --baseline path/to/output.json

# 直接使用 promptfoo CLI
cd evals/promptfoo
PROMPTFOO_PYTHON=./run-python.sh NOESIS_EVAL_SCOPE=full NOESIS_EVAL_TAG=baseline \
  npx promptfoo@latest eval
```

环境变量（`python -m evals` 会自动设置）：

| 变量 | 说明 |
|------|------|
| `NOESIS_EVAL_TAG` | run 标签 |
| `NOESIS_EVAL_SCOPE` | `testpoints` / `cases` / `full` |
| `NOESIS_EVAL_ITEM_ID` | 仅跑指定 id |
| `NOESIS_EVAL_LIMIT` | 仅跑前 N 条 |
| `NOESIS_EVAL_MOCK_JUDGE` | `1` 时 coverage 用名称匹配 mock |
| `NOESIS_EVAL_DATASET` | 自定义 dataset.jsonl 路径 |

## 指标

| 指标 | promptfoo metric | 含义 | 何时计算 |
|------|------------------|------|----------|
| **L0** | `l0` | 无 error、JSON 字段齐全 | 始终 |
| **coverage** | `point_coverage_recall` | LLM Judge → recall | `testpoints` / `full` |
| **rag** | `rag_hit_at_3` | retrieval_trace vs expected_rag | `cases` / `full` |

阈值见 `eval_targets.json`（`point_coverage_recall_min`、`rag_hit_at_3_min`）。

## CI

`pytest tests/test_eval_*.py` 直接测 scorer / runner，不调 DashScope、不依赖 promptfoo。
