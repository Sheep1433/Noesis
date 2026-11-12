# Delta: agent-harness — 中间件矩阵收窄

## MODIFIED Requirements

### Requirement: 各 Agent Profile SHALL 使用确定的 Middleware 集合

最终装配 SHALL 满足下列行为矩阵；"可选"能力只有在配置或 Profile 明确启用时才出现。Profile 集合随形态收窄（见 `agent-profiles`）调整：`FAULT_OPERATION_QA` 行移除，`Super/Fault 子 Agent` 行收窄为 `SUPER 子 Agent`。

| Profile | 必需能力 | 可选能力 |
|---|---|---|
| `COMMON_QA` | DynamicContext、ToolResultBudget、Compaction、PatchToolCalls、ToolFailure | Snip、DeferredToolFilter、manual compact、call limits |
| `SUPER_AGENT_QA` | COMMON 全部 + ReadBeforeWrite、DurableContext、Filesystem、RefreshingSkills、RefreshingMemory、Todo、SubAgent、DeferredToolFilter | Snip、manual compact、HITL、call limits |
| SimpleMCP | DynamicContext、ToolResultBudget、Compaction、DeferredToolFilter、PatchToolCalls、ToolFailure | Snip、manual compact、call limits |
| SUPER 子 Agent | DeepAgents isolated task context、ToolResultBudget、Compaction、PatchToolCalls、ToolFailure | ReadBeforeWrite、RefreshingSkills、Filesystem、DeferredToolFilter、Snip、局部 call limits |

#### Scenario: 未配置能力不出现

- **WHEN** Profile 没有 HITL、retry、model limit 或 tool limit 配置
- **THEN** 最终装配 SHALL NOT 含对应能力
