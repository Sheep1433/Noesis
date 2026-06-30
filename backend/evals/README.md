# 评测

`evals` 为评测包根目录，**不直接跑分**。各场景使用独立子模块：

| 场景 | 命令 | 状态 |
|------|------|------|
| 测试用例 Agent | `uv run python -m evals.case` | 已实现 |
| Agent / BrowseComp | `uv run python -m evals.agent.browsecomp` | 已实现 |
| Agent / WildClawBench | `uv run python -m evals.agent.wildclaw` | 已实现 |
| Agent / Terminal-Bench | `./evals/agent/harbor/run.sh` | 已实现 |
| 消息压缩 | `uv run python -m evals.compression` | 已实现 |
| 深度研究负载测试 | `uv run locust -f evals/loadtest/locustfile.py` | 已实现 |

```bash
cd backend
uv run python -m evals    # 仅打印上表说明
```

OpenSpec：`openspec/specs/agent-offline-eval/spec.md`、`openspec/specs/test-case-agent-eval/spec.md`

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

指标：**阶段 A**（L0、`point_coverage_recall`、`point_coverage_precision`）、**阶段 B**（两路 RAG Recall@3/Hit@3、`document_context_present`）。

```
evals/case/
  README.md
  report.py                   # 跑分后汇总指标、写 summary
  results/<tag>/              # 默认 promptfoo JSON + *-summary.json
  testpoints/
    golden/                   # 金标准源（prd_*.yaml）
    golden_loader.py
    generate_eval_dataset.py  # 从 documents/ + golden/ 生成 promptfooconfig
    promptfooconfig.yaml      # 运行时配置（由脚本生成）
    documents/
  rag/
    promptfooconfig.yaml
    corpus/test_cases/
    ingest.py
  shared/                     # assertions、judge
```

```bash
uv run python -m evals.case --phase testpoints --tag baseline
uv run python -m evals.case --phase stage-a --tag baseline   # 同上别名
uv run python -m evals.case --phase rag --tag rb-baseline
uv run python -m evals.case --phase stage-b --tag rb-baseline # 同上别名
uv run python -m evals.case.rag.ingest --map-only
uv run python -m evals.case.rag.ingest --reset
uv run python -m evals.case --phase testpoints --tag debug --item-id prd_001
```

阶段 A 金标准源在 `testpoints/golden/*.yaml`；运行时写入 `promptfooconfig.yaml` 的 `golden_test_points_json`。**不**使用 `dataset.jsonl`。跑分结束后默认写入 `results/<tag>/` 并在控制台打印 recall/precision 汇总。

RAG 集成测（pytest，默认 skip）：`NOESIS_CASE_RAG_EVAL=1` + 先 `evals.case.rag.ingest`。

coverage 走 Python 确定性 scorer（`shared/coverage_scorer.py`）；borderline 可启用 LLM 仲裁。详见 `evals/case/README.md`。

---

## 2. Agent 评测（按 benchmark 分子模块）

与 `evals.case` 相同：**每个官方评测集一个目录、一个入口**，无中间编排层。

```
evals/agent/
  _agent.py                 # DeepResearchAgent 共用执行
  browsecomp/
    official.py               # openai/simple-evals BrowseComp（vendored）
    __main__.py               # uv run python -m evals.agent.browsecomp
    results/<tag>/
  wildclaw/
    __main__.py               # 调官方 script/run.sh noesis
    worker.py                 # WildClawBench backend 回调
    noesis_agent.py           # 打入 vendor 的 agent 文件
    results/<tag>/
  harbor/
    run.sh                    # Harbor + Claude Code → terminal-bench@2.0
    README.md
```

### BrowseComp

```bash
uv run python -m evals.agent.browsecomp --tag bc-smoke --num-examples 5
```

官方 CSV + `BrowseCompEval` + 官方 `GRADER_TEMPLATE` → 指标 **accuracy**。结果：`browsecomp/results/<tag>/summary.json`。

### WildClawBench

```bash
git clone https://github.com/InternLM/WildClawBench vendor/WildClawBench
uv run python -m evals.agent.wildclaw --tag wc -- --category 02_Code_Intelligence --parallel 1
```

自动打入 `noesis` backend，评分走上游 Docker grader。结果：`wildclaw/results/<tag>/`。

### Terminal-Bench（Harbor + Claude Code）

```bash
cd backend
./evals/agent/harbor/run.sh --n-tasks 1 --job-name smoke
./evals/agent/harbor/run.sh --n-tasks 10 --job-name tbench-10
```

前置：Docker、`uv tool install harbor`、本机 `claude` CLI、`~/.claude/settings.json`。产物：`evals/agent/harbor/results/<job-name>/`。

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

---

## 4. 深度研究负载测试（`evals.loadtest` + Locust）

对运行中的后端发 HTTP 请求，压测 `DEEP_RESEARCH_QA` SSE 链路（与离线 eval 不同，走真实 API）。

```
evals/loadtest/
  locustfile.py
  sse_client.py
  queries.py
  data/queries.jsonl    # 5 条压测 query
  __main__.py           # 打印运行说明
```

```bash
uv sync --extra loadtest
uv run python -m evals.loadtest
uv run locust -f evals/loadtest/locustfile.py --host=http://127.0.0.1:8089
uv run locust -f evals/loadtest/locustfile.py --host=http://127.0.0.1:8089 \
  --headless -u 1 -r 1 --run-time 30m --only-summary
```

单用户（admin）、每请求新 session；客户端不设超时，等后端 SSE 自然结束。指标：`deep_research_stream`（端到端）、`deep_research_ttft`、`deep_research_tool_calls`。

`sse_client.consume_sse_stream` 读到 `data: [DONE]` 才计为成功端到端（与前端一致）；**提前断开**不影响服务端 partial 落库（见 `docs/prd/platform/SSE流式数据设计.md` §3.3、§6.4）。压测验证落库时请查 `t_chat_message` 同一 session 仅一条 assistant 行。
