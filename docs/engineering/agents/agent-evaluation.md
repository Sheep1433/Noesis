# Agent 评测运行指南

> 状态：Current  
> 关联 OpenSpec：`openspec/specs/offline-evals/spec.md`

本文说明 Noesis Agent 评测的入口、运行时间、结果位置和常见失败。命令默认从仓库根目录开始执行。

## 1. 先跑哪条路线

| 路线 | 被测对象 | 外部依赖 | 适合场景 |
|---|---|---|---|
| BrowseComp | `SuperAgent` | LLM、Web | 优先验证 Agent Harness、搜索和多步推理 |
| Agentic RAG | `GeneralQAAgent` | LLM、Qdrant、已入库知识库 | 验证 Harness KB Tool 和来源召回 |
| Harbor | Noesis terminal Agent | Python 3.13、Harbor、Docker、LLM | 验证容器终端任务 |

第一次验证推荐 BrowseComp 单题。它不要求 Qdrant 或 Docker，并且真实经过：

```text
BrowseComp → SuperAgent → Noesis Harness → stream_agent_events
           → AgentEventCollector → grader → summary.json
```

## 2. BrowseComp 单题 smoke

```bash
cd backend

uv run python -m evals.agent.browsecomp \
  --tag bc-smoke \
  --num-examples 1 \
  --time-budget 300 \
  --model-id flash \
  --grader-model-id flash
```

参数说明：

- `--tag`：本次结果目录名，建议每次使用不同名称。
- `--num-examples`：抽取题目数；smoke 固定为 `1`。
- `--time-budget`：每道题中被测 Agent 的秒数预算，不包含 grader。
- `--model-id`：被测 Agent 使用的 Harness model catalog id。
- `--grader-model-id`：裁判模型。正式对比时应固定，不要随被测模型改变。

首次运行会下载 BrowseComp CSV，之后读取缓存：

```text
backend/evals/agent/browsecomp/data/browse_comp_test_set.csv
```

结果位于：

```text
backend/evals/agent/browsecomp/results/<tag>/summary.json
backend/evals/agent/browsecomp/results/<tag>/convos.jsonl
```

查看结果：

```bash
jq . evals/agent/browsecomp/results/bc-smoke/summary.json
jq . evals/agent/browsecomp/results/bc-smoke/convos.jsonl
```

成功写出结果表示评测流程完成，不表示题目回答正确。`accuracy: 0` 可能是答案错误，也可能是 Agent 超时；继续检查 `convos.jsonl` 中的 assistant 内容。

## 3. 为什么一次操作可能等待很久

BrowseComp 总耗时近似为：

```text
题目数 ×（Agent 实际耗时 + grader 实际耗时）+ 数据集与初始化耗时
```

`--time-budget` 只限制 Agent。即使设置为 `1` 秒，grader 若耗时 60 秒，整条命令仍可能运行约 61 秒。

2026-07-30 的两次实测：

| tag | Agent budget | 总耗时 | 结果 |
|---|---:|---:|---|
| `smoke-flash-20260730` | 240 秒 | 244.27 秒 | Agent 超时，流程正常落盘 |
| `timeout-contract-20260730` | 1 秒 | 61.71 秒 | Agent 超时；主要额外耗时来自 grader |

当 Codex 任务显示约 47 分钟时，它统计的是整轮开发操作，不是单个 Agent 调用。该轮还包括 Harbor Python 3.12 环境解析和依赖构建、两次 BrowseComp、模型连通性探测、测试与代码审查。现有结果中最长的单次 BrowseComp 是 244.27 秒。

## 4. 快速判断失败位置

### `Error: timeout after <N>s`

Agent 没在预算内完成，取消链路正常。可提高 `--time-budget`，或检查 `convos.jsonl` 中题目是否本身需要大量搜索。

### grader 超时或 CLI 没有写 summary

Agent 完成后，grader 仍会单独调用 LLM。先确认模型端点：

```bash
cd backend
uv run python - <<'PY'
from langchain_core.messages import HumanMessage
from noesis.llm import get_llm

response = get_llm(model_id="flash").invoke(
    [HumanMessage(content="Reply with exactly: OK")]
)
print(response.content)
PY
```

### 只有 LangChain `RunnableConfig` warning

这是类型提示，不代表评测失败。以进程退出码、`summary.json` 和 `convos.jsonl` 为准。

## 5. Agentic RAG

前置条件：Qdrant 可用，fixture 中指定的 collection 已完成入库。

```bash
cd backend

uv run python -m evals.agent.rag \
  --dataset fixtures/sample.jsonl \
  --model-id flash \
  --time-budget 180
```

结果至少包含：完成状态、KB Tool 是否调用、来源召回、回答、耗时和错误。

## 6. Harbor 单题

Noesis Harbor 入口基于官方 `harbor.agents.base.BaseAgent`，直接使用 Harbor `BaseEnvironment` 和 Noesis Harness，不再启动 Worker 或 TCP proxy。

```bash
cd backend

./evals/agent/harbor/run-noesis.sh
```

前置条件：

- Docker 可用；
- `uv` 可取得 Python 3.12；
- 能安装 Harbor 0.15 的依赖；
- `OPENCODE_API_KEY` 或对应模型凭据可用。

首次运行前建议先拉取 smoke 镜像，避免 Harbor 在环境准备阶段长时间没有进度输出：

```bash
docker pull alexgshaw/fix-git:20260403
```

如果日志停在 `docker compose up --detach --wait` 且 `docker image inspect alexgshaw/fix-git:20260403` 失败，说明仍在等待镜像，不是 Agent 已开始执行。

结果位于 `backend/evals/agent/harbor/results/<job-name>/`，使用下面的命令查看：

```bash
harbor view evals/agent/harbor/results/noesis-smoke
```

## 7. 提交前验证

修改公共评测 runner、collector 或 timeout 行为后执行：

```bash
cd backend

uv run pytest \
  tests/test_eval_agent_runtime.py \
  tests/test_eval_agent_browsecomp.py \
  tests/test_eval_agentic_rag.py \
  tests/test_harness_eval_overrides.py \
  -q
```

评测实现入口：

- `backend/evals/agent/runtime.py`
- `backend/evals/agent/_agent.py`
- `backend/evals/agent/browsecomp/__main__.py`
- `backend/evals/agent/rag/runner.py`
- `backend/evals/agent/harbor/noesis_agent.py`
