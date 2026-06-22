## ADDED Requirements

### Requirement: Agent 离线评测与测试用例评测目录隔离

系统 SHALL 在 `backend/evals/agent/` 提供 Agent 端到端离线评测。Agent 评测 **SHALL NOT** 与 `evals/case/` 下测试用例评测共用 runner 或 `dataset.jsonl`。Agent 评测入口 SHALL 为 `uv run python -m evals.agent`；测试用例评测入口 SHALL 为 `uv run python -m evals.case`（见 `test-case-agent-eval` 规格）。

#### Scenario: Agent 与测试用例评测独立执行

- **WHEN** 开发者运行 `uv run python -m evals.agent --tag dr-baseline`
- **THEN** 系统 SHALL 仅加载 `evals/agent/datasets/` 并执行 Agent runner，**SHALL NOT** 读取 `evals/case/datasets/test_case/`

#### Scenario: 根包不替代场景入口

- **WHEN** 开发者运行 `uv run python -m evals --tag baseline`
- **THEN** 系统 **SHALL NOT** 启动测试用例或 Agent 评测；开发者 SHALL 改用 `evals.case` 或 `evals.agent`

### Requirement: 评测 DeepResearchAgent

Agent 离线 runner SHALL 固定调用 `DeepResearchAgent`（与线上 `qa_type=DEEP_RESEARCH_QA` 一致的工具栈与中间件）。**SHALL NOT** 支持切换为其它 qa_type 或 Agent 类。

#### Scenario: 深度研究任务成功执行

- **WHEN** 数据集条目 `query` 非空
- **THEN** runner SHALL 在隔离工作区内调用 `DeepResearchAgent` 完成任务，并记录 `completed`、`latency_ms` 与工具调用统计

#### Scenario: 不得误用其它 Agent

- **WHEN** 执行 `evals.agent` 离线评测
- **THEN** runner **SHALL NOT** 调用 `CommonReactAgent`、`FaultOperationAgent` 或测试用例 `case_graph`

### Requirement: 数据集排除生活类 WildClawBench 任务

`evals/agent/datasets/deep_research/dataset.jsonl` 中的任务 SHALL 聚焦检索、代码智能、结构化报告合成及（可选）安全拒绝类场景。数据集 **SHALL NOT** 包含依赖邮件客户端、日历、即时通讯、电商生活流程的评测项。每条任务 SHOULD 在 `provenance` 中标注迁移来源。

#### Scenario: 检索类任务纳入

- **WHEN** 任务要求 Agent 通过联网检索公开信息并写入工作区文件
- **THEN** 该任务 SHALL 允许出现在数据集中，且 `category` MAY 为 `search_retrieval`

#### Scenario: 邮件类任务不得纳入

- **WHEN** 任务规格要求发送邮件、读取收件箱或操作日历邀请
- **THEN** 该任务 **SHALL NOT** 出现在 Noesis Agent 离线评测数据集中

### Requirement: 工作区隔离与种子资产

每条评测任务 SHALL 在运行前获得独立工作区目录（位于 `.data/agent_workspace/` 下 eval 专用路径）。若条目声明 `workspace_seed`，runner SHALL 将种子目录复制到该工作区后再启动 Agent。单次 run 内不同 `item_id` **SHALL NOT** 共享可写工作区。

#### Scenario: 带种子代码的任务

- **WHEN** 条目含 `workspace_seed` 指向 `workspaces/dr_code_sam3_debug/`
- **THEN** runner SHALL 在 Agent 启动前将种子文件复制到本次任务工作区

### Requirement: 混合评分在 Agent 结束后执行

系统 SHALL 在 Agent 运行结束后对工作区产物与最终文本执行混合评分：确定性规则 criteria → 工作区副作用检查 →（若存在）`semantic_rubric` 的 LLM Judge。评分逻辑 **SHALL NOT** 在 Agent 执行期间向 Agent 暴露 `ground_truth`。

#### Scenario: 规则项判定文件产物

- **WHEN** `ground_truth.criteria` 含 `type=file_exists` 且 Agent 已在约定路径创建文件
- **THEN** 评分结果 SHALL 将该 criterion 记为 `passed=true`，且不调用 LLM

#### Scenario: 语义 rubric 调用真实 LLM

- **WHEN** 条目含非空 `semantic_rubric` 且规则项已执行完毕
- **THEN** 系统 SHALL 使用 `get_llm()` 进行 Judge，**SHALL NOT** 提供 mock 开关

### Requirement: 评测结果持久化与汇总

每次 `--tag` 运行 SHALL 在 `backend/evals/agent/results/<tag>/` 下写入 `runs/<item_id>.json` 及 `summary.json` / `summary.md`。

#### Scenario: 单条任务结果可查

- **WHEN** 某条 `item_id` 评测完成
- **THEN** 系统 SHALL 写入 `runs/<item_id>.json`

#### Scenario: 与历史 tag 对比

- **WHEN** CLI 传入 `--compare-to results/<baseline_tag>`
- **THEN** 系统 SHALL 在 `summary.md` 中输出各 `item_id` 分数差值

### Requirement: CLI 过滤与手动执行

`evals.agent` CLI SHALL 支持 `--tag`（必填）、`--item-id`、`--limit`、`--dataset`；环境变量 `NOESIS_AGENT_EVAL_*`。全量评测 **SHALL NOT** 作为 CI 必需步骤。

#### Scenario: 调试单题

- **WHEN** 开发者执行 `uv run python -m evals.agent --tag debug --item-id dr_code_sam3_debug`
- **THEN** 系统 SHALL 仅运行该条目一次并输出结果
