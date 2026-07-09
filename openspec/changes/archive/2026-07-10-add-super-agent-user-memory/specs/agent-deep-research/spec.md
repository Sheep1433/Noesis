## REMOVED Requirements

### Requirement: qa_type 路由至 DeepResearchAgent

**Reason**: 由 `agent-super-agent` 的 `SuperAgent` + `SUPER_AGENT_QA` 完全替代；深度调研降为 Skill 触发的一种任务形态。

**Migration**: 客户端与评测将 `DEEP_RESEARCH_QA` 改为 `SUPER_AGENT_QA`；删除 `DeepResearchAgent` 类与 `agent-deep-research` 主规格。

### Requirement: create_noesis_agent 装配深度研究能力栈

**Reason**: 合并入 `agent-super-agent` 的 SuperAgent 装配要求（含 MemoryMiddleware、task-worker）。

**Migration**: 见 `openspec/changes/add-super-agent-user-memory/specs/agent-super-agent/spec.md`。

### Requirement: CompositeBackend 工作区与 Skills 路由

**Reason**: 工作区与 Skills 规则保留在 `agent-runtime-paths` 与 `agent-sandbox`；本 Requirement 标题下的深度研究专属表述由 SuperAgent 规格承接。

**Migration**: 使用 `create_agent_backend` + `/memory/` 扩展，见 `agent-user-memory`。

### Requirement: SkillsMiddleware 与 Available Skills 行为

**Reason**: 迁移至 `agent-super-agent`，子 Agent 更名为 `task-worker`，移除 research-worker 专属表述。

**Migration**: 见 `agent-super-agent` Skills 与 Web 工具 Requirement。

### Requirement: summary_offload 与 StateBackend 适用性

**Reason**: 行为不变，改由 `agent-super-agent` 引用 `agent-runtime-paths` 中 summarization 条款；不单独保留深度研究 spec 副本。

**Migration**: 实现时 SuperAgent 继续挂载 `SummarizationOffloadMiddleware`，无需改 offload 路径规则。

### Requirement: 深度研究 system prompt

**Reason**: `deep_research.py` 删除，由 `super_agent.py` 统一 prompt。

**Migration**: 仅使用 `PromptProfile.SUPER_AGENT` / `SUPER_AGENT_SUB`。

### Requirement: SSE 与子 Agent 展示（research-worker）

**Reason**: `research-worker` 更名为 `task-worker`；展示规则仍在 `platform-chat` + `agent-super-agent` 中定义。

**Migration**: 前端 `SubagentCollapse` 期望 `subagent_type=task-worker`。
