## Why

当前 `evals.agent` 用手写 `dataset.jsonl` + 自研 `scoring.py`，分数无法与 WildClawBench / BrowseComp leaderboard 对齐，外部也不认可。需要改为 **直接接入上游官方评测集与官方评分链路**，Noesis 只提供 **Agent 适配层**，不自造题目、不自造 rubric。

## What Changes

- **BREAKING**：废弃 `datasets/deep_research/dataset.jsonl` 手工子集；`evals.agent` 变为 **上游 Harness 编排 CLI**。
- 接入 **WildClawBench 官方仓库**：全量 60 题、`eval/run_batch.py` + Docker 镜像 + 官方 hybrid grader。
- 接入 **BrowseComp 官方实现**：`openai/simple-evals` 的 `BrowseCompEval` + 官方 CSV 数据源 + 官方 `GRADER_TEMPLATE`。
- **不**把 OpenHands Index 当作独立 suite 跑分；代码向 benchmark（SWE-Bench、GAIA 等）**按需**通过 `OpenHands/benchmarks` 或 `swebench` 官方 harness 接入，与 DeepResearch 线分离。
- Noesis 实现：**Harness 适配器**（把 `DeepResearchAgent` 接到上游 `SamplerBase` / `agent-backend` 接口），评分 **100% 委托上游**。
- 结果输出对齐上游格式（WildClawBench `results/`、simple-evals `EvalResult`），另存 Noesis 汇总仅作本地对比。

## Capabilities

### Modified Capabilities

- `agent-offline-eval`：从自研 jsonl runner 改为官方 benchmark 适配编排；WildClawBench + BrowseComp 为 Agent 线必选上游；OpenHands/benchmarks 仅作代码类 benchmark 可选扩展。

## Impact

| 区域 | 变更 |
|------|------|
| `backend/evals/agent/` | 适配器 + subprocess/uv 调上游；删除自研 `scoring.py` 主路径 |
| 依赖 | git submodule 或 uv optional：`WildClawBench`、`simple-evals`；Docker 用于 WildClawBench |
| 文档 | `evals/README.md` 写明上游版本 pin 与复现命令 |
