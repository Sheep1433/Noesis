## Why

Noesis 需要与「测试用例生成」分离的 **深度研究 Agent 端到端离线评测**，以及 **消息压缩（Summarization offload）离线评测**，用于在需要时手动跑分、熟悉 Agent 评测流程。现有 `evals/case/` 仅覆盖测试用例 LangGraph 节点；`SummarizationOffloadMiddleware` 尚无「改配置 → 重跑 → 对比分数」的闭环。

同时，测试用例评测线中的质量门阈值、`mock-judge`、CI 冒烟等机制在开源场景下使用频率低，应精简为「需要时手动跑、始终调用真实模型、只汇报指标」。

## What Changes

- 新增 **`agent-offline-eval`** 能力：`backend/evals/agent/` 目录、数据集、runner、混合评分与 CLI。
- 新增 **`message-compression-eval`** 能力：`backend/evals/compression/` 目录、会话 fixture、probe 题库、LLM Judge 多维 rubric 与 CLI；评测对象为首版 **`SummarizationOffloadMiddleware`**（`get_llm(purpose="summarization")` 摘要路径），方法论参考 [hermes-compression-eval](https://github.com/NousResearch/hermes-compression-eval)（fixture → 压缩 → probe 作答 → Judge 打分 → baseline 对比）。
- Agent 评测对象仅为 **`DeepResearchAgent`**（`qa_type=DEEP_RESEARCH_QA`），与线上一致。
- 从 WildClawBench / 同类长程 benchmark **迁移典型任务**至 Agent 数据集（检索、代码智能、创意合成等），**排除**邮件、日历、社交通讯等生活类任务。
- Agent 评分：**规则检查 → 工作区副作用审计 → LLM Judge 语义兜底**；压缩评分：**probe 期望 + 多维 rubric LLM Judge**；均使用真实模型，无 mock。
- 精简测试用例评测线：移除 `eval_targets.json`、阈值质量门、`--mock-judge`、runner smoke 测试；coverage / rag 在 promptfoo 中仅汇报分数。

## Capabilities

### New Capabilities

- `agent-offline-eval`：`DeepResearchAgent` 离线评测的数据集、runner、混合评分与 CLI。
- `message-compression-eval`：会话 fixture 驱动的消息压缩/摘要质量评测（probe + 多维 rubric + baseline 对比）。

### Modified Capabilities

- `test-case-agent-eval`：评测入口改为 `evals.case`；代码迁至 `evals/case/`；去除质量门与 mock Judge。
- `agent-test-case`：离线评测章节中的数据集路径与 CLI 入口与实现对齐。

## Impact

- **后端**：`evals/case/`（测试用例线，已从 `evals/` 根迁出）；新增 `evals/agent/`、`evals/compression/`；`evals/__main__.py` 仅作场景导航。
- **评测对象**：`SummarizationOffloadMiddleware`（`backend/agent/middlewares/summary_offload_middleware.py`）；Agent 线为 `DeepResearchAgent`。
- **API/SSE**：评测 runner 直接调用中间件或 Agent，**不新增** REST 端点。
- **成本**：全量跑分需真实 LLM；压缩评测每条 fixture 含多道 probe（continuation + Judge），按 `--fixture` / `--limit` 控制；无 CI 强制门禁。
