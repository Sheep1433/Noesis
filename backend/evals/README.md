# 评测

`evals` 为评测包根目录，**不直接跑分**。各场景使用独立子模块：

| 场景 | 命令 | 状态 |
|------|------|------|
| 测试用例 Agent | `uv run python -m evals.case` | 已实现 |
| Agent / BrowseComp | `uv run python -m evals.agent.browsecomp` | 已实现 |
| Agent / Terminal-Bench | `./evals/agent/harbor/run.sh` | 已实现 |
| 消息压缩 | `uv run python -m evals.compression` | 已实现 |
| 深度研究负载测试 | `uv run locust -f evals/loadtest/locustfile.py` | 已实现 |
| 知识库检索（单集合） | `uv run python -m evals.kb.run --collection <name>` | 已实现 |

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

## 各评测线如何查看结果

Noesis **没有**统一的评测结果 Web 页面；各子模块产物与查看方式不同：

| 评测线 | 产物目录 | 专用 Web UI | 推荐查看方式 |
|--------|----------|-------------|--------------|
| 测试用例 `evals.case` | `evals/case/results/<tag>/` | **有**（promptfoo） | 控制台汇总 + `npx promptfoo view` |
| BrowseComp | `evals/agent/browsecomp/results/<tag>/` | **无** | `summary.json` / `convos.jsonl`；可选 Langfuse trace |
| Terminal-Bench（Harbor） | `evals/agent/harbor/results/<job>/` | **有**（Harbor） | `harbor view evals/agent/harbor/results/<job>` |
| 消息压缩 | `evals/compression/results/<tag>/` | **无** | `summary.json`；`--compare-to` 对比历史 |
| 知识库检索 `evals.kb` | 无持久化目录 | **无** | 命令行直接输出 Recall@K / Hit@K |
| 负载测试 Locust | Locust Web（运行时） | **有**（Locust） | `http://localhost:8089`（仅压测进行中） |

### 测试用例（promptfoo）

跑分结束控制台打印 recall/precision，并写入：

```
evals/case/results/<tag>/<phase>.json
evals/case/results/<tag>/<phase>-summary.json
```

Web 查看（需 Node.js）：

```bash
cd backend/evals/case/testpoints   # 或 rag，与 --phase 对应
npx promptfoo@latest view
```

控制台若打印了 `eval id`，在 promptfoo UI 里按该 id 定位本次跑分。

### BrowseComp（无专用结果页）

**整批题目跑完后**才写入（跑的过程中目录可能为空）：

```
evals/agent/browsecomp/results/<tag>/
  summary.json    # accuracy、耗时、题数
  convos.jsonl    # 每行一题：{ "index", "convo": [user, assistant] }
```

```bash
# 汇总
jq . evals/agent/browsecomp/results/bc-smoke-new-12/summary.json

# 逐题（每行一题）
jq . evals/agent/browsecomp/results/bc-smoke-new-12/convos.jsonl
```

终端结束时会打印 `BrowseComp accuracy: ...` 与 `Results: ...` 路径。

**过程/trace（非成绩表）**：配置 `evals/.env` 后可在 Langfuse 按 `eval_tag=<tag>`、`eval_line=agent` 筛选；整次 session 为 `browsecomp-<tag>`，单题为 `browsecomp-<uuid>`。单题工作区与卸載文件在 `.data/users/eval-browsecomp/sessions/`。

BrowseComp 走仓库内 Python 模块（`uv run python -m evals.agent.browsecomp`），直接调 Noesis `SuperAgent`，因此**未**集成 Harbor / promptfoo 类 viewer。

### Terminal-Bench（Harbor 自带 Web UI）

Harbor 通过 **外部 CLI + Docker** 跑题（`./evals/agent/harbor/run*.sh` → `harbor run`），产物在：

```
evals/agent/harbor/results/<job-name>/
```

本地 Web 查看（Harbor 提供，非 Noesis 前端）：

```bash
cd backend
harbor view evals/agent/harbor/results/<job-name>
```

**Harbor 残留容器**：任务结束后部分容器会以 `sleep infinity` 保持运行，便于查看挂载日志。评测结果已落在 `results/<job>/` 且不再调试时，可停止并删除，例如：

```bash
docker stop <container-id> && docker rm <container-id>
# 或批量清理已退出的 Harbor 相关容器
docker container prune
```

镜像 `alexgshaw/*` 为 Terminal-Bench 官方任务环境，删除容器**不会**删镜像；下次 `harbor run` 会按需复用本地镜像。

### 消息压缩

```
evals/compression/results/<tag>/summary.json
```

```bash
uv run python -m evals.compression --tag after-tweak --compare-to results/compress-baseline
```

### 知识库检索

无 `results/<tag>/` 目录；指标在命令行 stdout。需要留档时自行重定向：

```bash
uv run python -m evals.kb.run --collection requirement_docs \
  > evals/kb/results/manual-$(date +%Y%m%d).log
```

### 负载测试（Locust）

压测进行中访问 Locust 自带 UI：`http://localhost:8089`。结束后仅保留终端 `--only-summary` 输出，无项目内持久化结果目录。

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

## 2. Agent 评测（BrowseComp + Harbor）

个人学习与日常回归推荐 **两条主线**：

1. **BrowseComp** — 多步检索 + 短答案（`SuperAgent` / 深度研究能力）
2. **Harbor + Terminal-Bench** — 终端任务执行（`harbor view` 看轨迹）

```
evals/agent/
  _agent.py                 # SuperAgent 共用执行
  browsecomp/
    official.py
    __main__.py               # uv run python -m evals.agent.browsecomp
    results/<tag>/
  harbor/
    run.sh                    # Harbor + Claude Code → terminal-bench@2.0
    README.md
    results/<job-name>/
```

### BrowseComp

```bash
uv run python -m evals.agent.browsecomp --tag bc-smoke --num-examples 5
```

首次运行会从官方 URL 下载 CSV 并缓存到 `evals/agent/browsecomp/data/`；离线重跑复用缓存。也可设置 `BROWSECOMP_CSV_PATH` 指向本地文件。

官方 CSV + `BrowseCompEval` → 指标 **accuracy**。结果：`browsecomp/results/<tag>/summary.json`。

### Terminal-Bench（Harbor + Claude Code）

```bash
cd backend
./evals/agent/harbor/run.sh --n-tasks 1 --job-name smoke
harbor view evals/agent/harbor/results/smoke
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

对运行中的后端发 HTTP 请求，压测 `SUPER_AGENT_QA` SSE 链路（与离线 eval 不同，走真实 API）。

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

---

## 5. 知识库检索（`evals.kb`）

单集合 JSONL 基准，计算 Recall@K / Hit@K；与 `evals.case --phase rag` 互补（后者测场景级 Agent RAG）。

```bash
cd backend
uv run python -m evals.kb.run --collection requirement_docs --dataset evals/kb/fixtures/sample.jsonl
```

可选 `--k`、`--query-params '{"use_reranker":false}'`。默认读取目标集合 MySQL `query_params` 并走 `KbRetrievalService` 全链路。

`sse_client.consume_sse_stream` 读到 `data: [DONE]` 才计为成功端到端（与前端一致）；**提前断开**不影响服务端 partial 落库（见 `docs/prd/platform/SSE流式数据设计.md` §3.3、§6.4）。压测验证落库时请查 `t_chat_message` 同一 session 仅一条 assistant 行。
