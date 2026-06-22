# 评测

`evals` 为评测包根目录，**不直接跑分**。各场景使用独立子模块：

| 场景 | 命令 | 状态 |
|------|------|------|
| 测试用例 Agent | `uv run python -m evals.case` | 已实现 |
| 深度研究 Agent | `uv run python -m evals.agent` | 已实现 |
| 消息压缩 | `uv run python -m evals.compression` | 已实现 |

```bash
cd backend
uv run python -m evals    # 仅打印上表说明
```

OpenSpec：`openspec/changes/add-agent-offline-eval/`

## Langfuse（评测专用项目）

三条评测线共用 **`backend/evals/.env`** 中的 Langfuse 凭据，与 `backend/.env` 隔离：

```bash
cp backend/evals/.env.example backend/evals/.env
# 填入评测专用 Langfuse 项目的 pk/sk
```

- 仅在 `eval_langfuse_run(...)` 上下文内临时注入 SDK 环境变量，**退出后恢复**，不污染主项目
- trace metadata：`source=noesis-eval`、`eval_line`（`case` / `agent` / `compression`）、`eval_tag`
- 未配置 `evals/.env` 时评测照常跑分，只是不上报 Langfuse

---

## 1. 测试用例（`evals.case` + promptfoo）

指标：**L0**、**coverage**、**rag**。手动离线跑分，需 Qdrant（cases/full）+ LLM。

```
evals/case/
  dataset.py
  runner.py
  scoring.py
  promptfoo/
  datasets/test_case/
```

```bash
uv run python -m evals.case --tag baseline
uv run python -m evals.case --tag debug --item-id tc_login_001 --scope testpoints
```

环境变量（推荐 `NOESIS_CASE_EVAL_*`，兼容旧 `NOESIS_EVAL_*`）：

- `NOESIS_CASE_EVAL_TAG` / `NOESIS_CASE_EVAL_SCOPE`
- `NOESIS_CASE_EVAL_ITEM_ID` / `NOESIS_CASE_EVAL_LIMIT` / `NOESIS_CASE_EVAL_DATASET`

coverage 使用真实 LLM Judge；可用 `--compare` 与历史 promptfoo 结果对比。

---

## 2. 深度研究 Agent（`evals.agent`）

评测 `DeepResearchAgent`（`DEEP_RESEARCH_QA`）。数据集：`evals/agent/datasets/deep_research/`（8 条，含检索 / 代码 / 报告 / 安全类）。

```
evals/agent/
  dataset.py
  runner.py
  scoring.py
  report.py
  datasets/deep_research/
    dataset.jsonl
    workspaces/
  results/<tag>/
```

```bash
uv run python -m evals.agent --tag dr-baseline
uv run python -m evals.agent --tag debug --item-id dr_code_sam3_debug
uv run python -m evals.agent --tag try1 --limit 3 --compare-to results/dr-baseline
```

环境变量：`NOESIS_AGENT_EVAL_TAG`、`NOESIS_AGENT_EVAL_ITEM_ID`、`NOESIS_AGENT_EVAL_LIMIT`、`NOESIS_AGENT_EVAL_DATASET`。

混合评分：规则 criteria（`file_exists` / `file_contains` / `json_field_min` / `file_not_exists`）占 70%，`semantic_rubric` LLM Judge 占 30%。Judge **始终**调用真实 `get_llm()`，无 mock 开关。

集成测试（默认 skip）：

```bash
NOESIS_AGENT_EVAL_INTEGRATION=1 uv run pytest tests/test_eval_agent_integration.py -q
```

---

## 3. 消息压缩（`evals.compression`）

评测 `SummarizationOffloadMiddleware`（`before_model` 摘要路径）。流程对齐 [hermes-compression-eval](https://github.com/NousResearch/hermes-compression-eval)：

```
fixture → driver.compress() → probe continuation (get_llm) → Judge 五维 0–5 (get_llm)
```

摘要模型：`get_llm(purpose="summarization")`；需 `summarization.enabled=true`。

```
evals/compression/
  driver.py
  grader.py
  rubric.py
  report.py
  fixture_loader.py
  fixtures/          # debug_session, feature_impl, config_build
  probes/
  results/<tag>/
```

```bash
uv run python -m evals.compression --tag compress-baseline
uv run python -m evals.compression --tag tweak --fixture debug_session --runs 3
uv run python -m evals.compression --tag after-tweak --compare-to results/compress-baseline
```

环境变量：`NOESIS_COMPRESSION_EVAL_TAG`、`NOESIS_COMPRESSION_EVAL_FIXTURE`、`NOESIS_COMPRESSION_EVAL_RUNS`。

五维 rubric：`accuracy`、`artifact_trail`、`context_awareness`、`continuity`、`completeness`。fixture 得分 = 全部 probe 的 `overall_probe_score` 中位数；`--runs N` 时对同一 fixture 多次取中位数。

集成测试（默认 skip）：

```bash
NOESIS_COMPRESSION_EVAL_INTEGRATION=1 uv run pytest tests/test_eval_compression_integration.py -q
```

可选：从真实会话 JSONL 脱敏导出 fixture 可自建脚本（参考 hermes `scrub_fixtures.py`），非阻塞。
