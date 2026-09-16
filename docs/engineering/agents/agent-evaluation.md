# Agent 评测运行指南

> 状态：Current  
> 关联 OpenSpec：`openspec/specs/offline-evals/spec.md`

本文说明 Noesis Agent 评测的入口、运行时间、结果位置和常见失败。命令默认从仓库根目录开始执行。

## 1. 先跑哪条路线

| 路线 | 被测对象 | 外部依赖 | 适合场景 |
|---|---|---|---|
| DeepResearch | `SuperAgent` | LLM、Web（代理可选） | 开放调研任务，验证 Harness、搜索与多步推理 |
| Agentic RAG | `GeneralQAAgent` | LLM、Qdrant、已入库知识库 | 验证 Harness KB Tool 和来源召回 |
| 记忆召回 | `SuperAgent` | LLM、LongMemEval 数据集 | 验证记忆检索与行为级召回 |

第一次验证推荐 DeepResearch 单题。它不要求 Qdrant 或 Docker，并且真实经过：

```text
DeepResearch → SuperAgent → Noesis Harness → stream_agent_events
           → AgentEventCollector → summary.json
```

## 2. DeepResearch 单题 smoke

```bash
cd backend

NOESIS_WEB_PROXY=http://127.0.0.1:7897 \
uv run python -m evals.agent.deepresearch \
  --tag dr-smoke \
  --limit 1 \
  --time-budget 600 \
  --model-id flash \
  --eval-user test
```

参数说明：

- `--tag`：本次结果目录名，建议每次使用不同名称。
- `--limit`：只跑前 N 题；smoke 固定为 `1`。
- `--time-budget`：每道题中被测 Agent 的秒数预算（默认 2700）。
- `--model-id` / `--model-user`：被测 Agent 使用的模型（catalog id 或用户绑定）。
- `--eval-user`：须为真实账号（默认 `test`）——子 Agent 会话血缘按 user_id 落库，假用户名会导致派发失败、主 Agent 单干。
- `NOESIS_WEB_PROXY`：web_fetch 的代理回退（直连失败时自动走代理重试），直连可达时可省略。

结果位于：

```text
backend/evals/agent/deepresearch/results/<tag>/articles.jsonl
backend/evals/agent/deepresearch/results/<tag>/summary.json
```

`articles.jsonl` 每行一题 `{id, prompt, article}`，可直接喂官方 RACE/FACT 判分脚本；评测线暂未接入判分，先产出报告原文。

## 3. 为什么一次操作可能等待很久

深度研究单题耗时近似为：

```text
题目数 × Agent 实际耗时 + 数据集与初始化耗时
```

`--time-budget` 只限制 Agent。单题涉及多轮子 Agent 派发与 web 检索，正常也可能运行数分钟到数十分钟。

## 4. 快速判断失败位置

### `Error: timeout after <N>s`

Agent 没在预算内完成，取消链路正常。可提高 `--time-budget`，或检查逐题记录中题目是否本身需要大量搜索。

### 判卷超时或 CLI 没有写 summary

RAG 与记忆线的判卷（judge）在 Agent 完成后仍会单独调用 LLM。先确认模型端点：

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

这是类型提示，不代表评测失败。以进程退出码、`summary.json` 和逐题记录为准。

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

## 6. 提交前验证

修改公共评测 runner、collector 或 timeout 行为后执行：

```bash
cd backend

uv run pytest \
  tests/test_eval_agent_runtime.py \
  tests/test_eval_agentic_rag.py \
  tests/test_eval_agent_memory.py \
  tests/test_harness_eval_overrides.py \
  -q
```

评测实现入口：

- `backend/evals/agent/runtime.py`
- `backend/evals/agent/_agent.py`
- `backend/evals/agent/deepresearch/__main__.py`
- `backend/evals/agent/rag/runner.py`
- `backend/evals/agent/memory/runner.py`
