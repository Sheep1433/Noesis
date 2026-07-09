## MODIFIED Requirements

### Requirement: 评测执行 SuperAgent / DeepResearchAgent

Agent 离线 runner（各 benchmark 子模块）SHALL 经 `NoesisRuntimeClient` 或 `AgentRunService` 执行，使用与线上相同的 `AgentProfile` 装配路径。Runner **SHALL NOT** 在 `evals/agent/` 内直接调用 `create_noesis_agent` 并手写 `astream_events` 循环。Harbor 线 **SHALL** 通过 `NoesisRuntimeClient` 注入 `ProxyHarborBackend`；**SHALL NOT** 维护独立于 Runtime factory 的 middleware 栈副本。

#### Scenario: 深度研究任务成功执行

- **WHEN** 数据集条目 `query` 非空
- **THEN** runner SHALL 构造 `AgentRuntimeContext` 并在隔离工作区内经 Runtime Client 完成任务，并记录 `completed`、`latency_ms` 与工具调用统计

#### Scenario: 不得误用其它 Agent

- **WHEN** 执行 `evals.agent.*` 离线评测
- **THEN** runner **SHALL NOT** 调用 `CommonReactAgent`、`FaultOperationAgent` 或测试用例 `case_graph` 作为 BrowseComp / Harbor 主线

#### Scenario: Harbor worker 与 BrowseComp 路径一致

- **WHEN** 开发者分别运行 `evals.agent.browsecomp` 与 `evals/agent/harbor/run.sh`
- **THEN** 两者 **SHALL** 均经 `noesis_runtime` 执行内核
- **AND** **SHALL NOT** 在 `harbor/noesis_worker.py` 内独立拼装 `create_noesis_agent` + `TodoListMiddleware` 栈
