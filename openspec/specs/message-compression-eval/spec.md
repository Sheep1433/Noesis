# message-compression-eval Specification

## Purpose
TBD - created by archiving change add-agent-offline-eval. Update Purpose after archive.
## Requirements
### Requirement: 消息压缩评测独立入口与目录

系统 SHALL 在 `backend/evals/compression/` 提供消息压缩离线评测，CLI 入口为 `uv run python -m evals.compression`。该评测 **SHALL NOT** 与 `evals.case` 或 `evals.agent` 共用 runner 或结果目录。

#### Scenario: 压缩评测独立执行

- **WHEN** 开发者运行 `uv run python -m evals.compression --tag compress-baseline`
- **THEN** 系统 SHALL 仅加载 `evals/compression/fixtures/` 与 `evals/compression/probes/`，**SHALL NOT** 启动 `DeepResearchAgent` 或测试用例 `case_graph`

### Requirement: 评测 SummarizationOffloadMiddleware 摘要路径

压缩评测 driver SHALL 对 fixture 中的消息列表调用与线上一致的 `SummarizationOffloadMiddleware`（经 `create_summary_offload_middleware` 创建）的 `before_model` 逻辑，产生压缩后的消息状态。摘要模型 SHALL 使用 `get_llm(purpose="summarization")`；未配置独立摘要模型时 SHALL 回退主模型参数（与 `llm/factory.py` 行为一致）。

#### Scenario: Fixture 触发摘要

- **WHEN** fixture 消息 token 数达到配置的摘要触发条件，或 fixture 声明 `compress_options.force`
- **THEN** driver SHALL 执行 tool 卸载（若适用）与 LLM 摘要，并返回压缩后的 `messages` 供后续 probe 使用

#### Scenario: 摘要关闭时评测失败

- **WHEN** `ModelConfig.summarization_enabled` 为 false
- **THEN** 压缩评测 CLI SHALL 在启动时失败并提示启用 `summarization`，**SHALL NOT** 静默跳过摘要

### Requirement: Fixture 与 Probe 数据格式

每条 fixture SHALL 为 JSON 文件，包含 `id`、`description` 与 `messages`（LangChain 兼容消息列表）。每条 fixture SHALL 有对应的 `probes/<id>.probes.json`，其中 `probes` 数组每项含 `id`、`type`（`recall` | `artifact` | `continuation` | `decision`）、`question`、`reference_answer`。Fixture 内容 **SHALL NOT** 包含真实密钥或用户 PII。

#### Scenario: Probe 类型覆盖

- **WHEN** 某 fixture 的 probe 题库加载完成
- **THEN** 该题库 SHOULD 至少包含两种 `type`，以覆盖事实召回与任务延续类问题

### Requirement: Probe 作答与多维 Rubric Judge

对每道 probe，系统 SHALL 使用 `get_llm()` 在**压缩后**的消息上下文上生成答案，再使用 Judge LLM 依据 `reference_answer` 与五维 rubric（`accuracy`、`artifact_trail`、`context_awareness`、`continuity`、`completeness`）对答案打 0–5 分。Judge **SHALL** 调用真实 LLM，**SHALL NOT** 提供 mock 或跳过 Judge 的环境开关。

#### Scenario: 单道 Probe 评分落盘

- **WHEN** 某 probe 完成 continuation 与 Judge
- **THEN** 结果 JSON SHALL 包含 `probe_id`、各维度分数、`judge_raw`（Judge 原始输出）及 `continuation_text`

### Requirement: 结果持久化与 Baseline 对比

每次 `--tag` 运行 SHALL 在 `backend/evals/compression/results/<tag>/` 下写入 `runs/<fixture_id>.json` 与 `summary.json` / `summary.md`。CLI SHALL 支持 `--compare-to results/<baseline_tag>`，在 summary 中输出各 fixture、各维度相对 baseline 的差值。CLI SHALL 支持 `--runs N`（同一 fixture 重复 N 次，报告中取各维度中位数）。

#### Scenario: 调参后对比 baseline

- **WHEN** 开发者修改摘要相关配置或 middleware 提示后执行 `--tag after-tweak --compare-to results/baseline`
- **THEN** `summary.md` SHALL 展示各 fixture 总分及五维分数与 baseline 的差值

### Requirement: CLI 过滤与手动执行

`evals.compression` CLI SHALL 支持 `--tag`（必填）、`--fixture`、`--runs`、`--compare-to`；环境变量 `NOESIS_COMPRESSION_EVAL_TAG`、`NOESIS_COMPRESSION_EVAL_FIXTURE`、`NOESIS_COMPRESSION_EVAL_RUNS`。全量压缩评测 **SHALL NOT** 作为 CI 必需步骤。

#### Scenario: 单 fixture 调试

- **WHEN** 执行 `uv run python -m evals.compression --tag debug --fixture debug_session --runs 1`
- **THEN** 系统 SHALL 仅评测该 fixture 一次并输出结果

