## Context

用户要求：**直接接入官方评测集，评分方法与 leaderboard 统一**，不要自研 jsonl + 自研 Judge。

### 各上游「加载 + 跑分」真相

| Benchmark | 官方加载 | 官方跑分框架 | 要不要用 OpenHands？ |
|-----------|----------|--------------|---------------------|
| **WildClawBench** | `tasks/` 任务 md + HuggingFace Docker 镜像 | `InternLM/WildClawBench` → `script/run.sh` → `eval/run_batch.py` + 容器内 hybrid grader | **不要**。OpenHands 不管这套 |
| **BrowseComp** | Azure CSV（加密 problem/answer） | `openai/simple-evals` → `BrowseCompEval` + 官方 `GRADER_TEMPLATE` LLM 判分 | **不要** |
| **SWE-Bench / GAIA / Commit0** | 各 benchmark 自有数据 | `OpenHands/benchmarks`（或 `swebench` 包） | **仅这类代码任务可选**；评的是 coding agent 能力，不是 DeepResearch 主线 |

**OpenHands/benchmarks** = 给 **OpenHands SDK Agent** 跑 SWE-Bench、GAIA 等的 Docker 流水线，**不是** WildClawBench/BrowseComp 的框架，**也不是** OpenHands Index 本身（Index 只是五柱聚合排行榜）。

### Noesis 角色

```
┌─────────────────────────────────────────────────────────┐
│  evals.agent（薄编排层）                                  │
│  uv run python -m evals.agent --benchmark wildclaw ...   │
└────────────┬──────────────────────────┬─────────────────┘
             │                          │
    ┌────────▼────────┐        ┌─────────▼──────────┐
    │ WildClawBench   │        │ simple-evals       │
    │ 官方 run_batch  │        │ BrowseCompEval     │
    │ + 官方 grader   │        │ + 官方 grader      │
    └────────┬────────┘        └─────────┬──────────┘
             │                          │
    ┌────────▼──────────────────────────▼──────────┐
    │  NoesisHarnessAdapter                         │
    │  DeepResearchAgent.run_agent → 工具/工作区     │
    └──────────────────────────────────────────────┘
```

## Goals / Non-Goals

**Goals:**

- WildClawBench：**60/60**，Docker + 官方 `eval/run_batch.py`，官方评分脚本产出可对比分数。
- BrowseComp：**1266** 题，官方 CSV + `BrowseCompEval`，accuracy 与 simple-evals 一致。
- 上游版本 **pin**（submodule commit / tag），文档写清复现命令。
- 实现 WildClawBench 新 backend：`--agent-backend noesis`（PR 上游或 vendor patch）。

**Non-Goals:**

- 不自建 `dataset.jsonl` 改编题、不自建 `semantic_rubric` 权重。
- 不把 OpenHands Index 当 suite 跑。
- 首版不要求 Noesis 进 SWE-Bench leaderboard（需另接 OpenHands/benchmarks + coding agent）。

## Decisions

### 1. WildClawBench 接入（主路径）

**依赖**：`vendor/WildClawBench` submodule（pin commit）。

**加载**：上游 `tasks/**/*.md` + `eval/` 配置；镜像 `wildclawbench-ubuntu:v1.3` 等按官方 README 拉取。

**运行**：

```bash
# 官方入口（Noesis 适配后）
cd vendor/WildClawBench
bash script/run.sh noesis --category all --parallel 4 --model <litellm-model-id>
```

**Noesis 适配**：在 `eval/run_batch.py` 增加 `agent-backend noesis`：

- 容器内或 host 侧调用 `evals.agent.adapters.wildclaw.NoesisBackend`
- 将任务 prompt 转为 `DeepResearchAgent.run_agent(query, session_id, ...)`
- 工作区、超时、`time_budget` 与 WildClawBench 任务 spec 对齐
- **评分**：完全使用上游 `eval/grade_*.py`，grading 资产仍在 agent 退出后注入

**CLI 封装**：

```bash
uv run python -m evals.agent --benchmark wildclaw --tag run1 \
  -- --category all --parallel 2 --model openrouter/...
```

`--` 后参数原样转给 `script/run.sh`。

### 2. BrowseComp 接入

**依赖**：`vendor/simple-evals` submodule 或 `uv` git dep。

**加载**：官方 URL

`https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv`

**运行**：

```python
from browsecomp_eval import BrowseCompEval
from evals.agent.adapters.browsecomp import NoesisBrowseCompSampler

sampler = NoesisBrowseCompSampler(...)  # 内部调 DeepResearchAgent + web 工具
grader = OpenAIChatSampler(model="gpt-4o")  # 与官方一致，grader 模型可配置
eval = BrowseCompEval(grader_model=grader, num_examples=None)
result = eval(sampler)  # 官方聚合 accuracy
```

**CLI**：

```bash
uv run python -m evals.agent --benchmark browsecomp --tag bc1 --num-examples 50
```

**评分**：**仅** `BrowseCompEval.grade_sample` + `GRADER_TEMPLATE`，禁止 Noesis 自写 rubric。

### 3. 要不要用 OpenHands/benchmarks？

| 场景 | 建议 |
|------|------|
| DeepResearchAgent + WildClawBench + BrowseComp | **不用** OpenHands |
| 未来要报 SWE-Bench Verified / GAIA 官方分 | **用** `OpenHands/benchmarks` 或原生 `swebench.harness`，并给 OpenHands 增加 `noesis` agent 后端 **或** 单独维护 `evals.agent-coding` 线 |
| 为了「Index 总分」 | Index 是加权聚合，**分别跑各官方 benchmark 后本地按 Index 公式加权**，不必跑 OpenHands 这个平台 |

**结论**：Agent 评测线（DeepResearch）**不需要** OpenHands 框架。只有当你要评 **修 GitHub issue / GAIA 代码调研** 且要和 SWE leaderboard 对齐时，才引入 `OpenHands/benchmarks`，且那是 **另一条 coding agent 线**，不是 WildClaw/BrowseComp 的替代品。

### 4. 目录布局（修订）

```
backend/evals/agent/
  __main__.py              # --benchmark wildclaw|browsecomp
  adapters/
    wildclaw.py            # WildClawBench noesis backend
    browsecomp.py          # SamplerBase → DeepResearchAgent
  vendor/                  # git submodule（或 repo 根 vendor/）
    WildClawBench/
    simple-evals/
  results/<tag>/           # 软链或复制上游输出 + manifest.json（上游 commit、命令）
```

删除或废弃：`dataset.py` 手写加载、`scoring.py` 自研 Judge（BrowseComp/WildClaw 路径）。

### 5. 结果可信度

每次 run 的 `manifest.json` **SHALL** 记录：

- `upstream_repo` + `commit_sha`
- 完整复现命令
- 上游原始 `summary` / `accuracy` 路径

对外引用分数时 **SHALL** 使用上游指标名（如 WildClawBench `score%`、`BrowseComp accuracy`），不发明 `overall_score` 加权。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| WildClawBench 需 Docker + 大镜像 | 文档 + `--category` 子集 |
| 上游无 noesis backend | vendor patch，长期 PR InternLM |
| BrowseComp grader 要 LLM | 与官方一致，单独配置 grader 模型 |
| simple-evals 已停更 | BrowseComp 实现稳定，pin commit即可 |

## Migration Plan

1. 添加 submodules + `evals.agent.adapters` 骨架。
2. BrowseComp：先通 `NoesisBrowseCompSampler` + 官方 `BrowseCompEval`（易验证）。
3. WildClawBench：Docker + `noesis` backend + 单题 smoke。
4. 全量 60 题 + 1266 题文档化复现。
5. 删除 `datasets/deep_research/` 手工集。

## Open Questions

- WildClawBench `noesis` backend：容器内跑 Agent 还是 host 跑 Agent、容器仅 grading？
- BrowseComp grader 模型：固定 `gpt-4o` 还是可配置（leaderboard 需注明）？
