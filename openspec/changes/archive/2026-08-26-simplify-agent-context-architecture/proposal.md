## Why

Noesis 当前把 DeerFlow 式细粒度能力收进五个宏观 runtime middleware，结果是 prompt、message repair、compaction、工具结果、retry、预算和观测在多个 hook 中交叉，还通过 `ContextVar` 传递隐式状态。旧循环来自 factory、场景模块与 eager package export 的 import 方向，不是 `middlewares` 位于 `agents` 之下；手写 inventory 与真实子 Agent 装配也已经分叉。

本变更不以“尽量少写 middleware”为目标。目标是复现 Claude Code 2.1.88 的上下文管理策略，同时采用 DeepAgents 风格的直接 factory 参数和 LangChain `create_agent()` 装配，并保持 DeerFlow 式完整 Agent runtime 包归属。LangChain/DeepAgents 与 Claude Code 语义不同或缺失时，Noesis 必须实现新的窄 middleware 或 runtime adapter。

## What Changes

- 以 Claude Code 的实际 pipeline 为行为基线：稳定来源重建、tool-result replacement、snip、micro-compaction、自动/增量/手动 compaction、summary PTL recovery、连续失败熔断、post-compact rebuild、deferred tools、file state 和 subagent 默认隔离。
- 使用 `factory.py + agents/{middlewares,backends,runtime} + agents 场景模块` 结构；`middlewares/` 内保持平铺；不增加 `capabilities/`、`ContextCompiler`、可执行 spec、多层 kernel 或第二套 Agent loop。
- `create_noesis_agent()` 直接接收 model、tools、system prompt、middleware、backend、subagents、skills、memory 等参数，一次生成完整 stack；inventory 从该实例列表生成，调用方不得事后 append。
- 直接采用契约符合的 DeepAgents/LangChain 能力：Filesystem、Skills、Memory、Todo、PatchToolCalls、HITL 和通用 call limits。
- Noesis 只自研八个窄 middleware：DynamicContext、DurableContext、ToolResultBudget、ToolFailure、ReadBeforeWrite、DeferredToolFilter、Compaction 和 Snip。
- Skills 与 Memory 继续继承 DeepAgents 实现，只增加 `RefreshingSkillsMiddleware` 与 `RefreshingMemoryMiddleware` 两个 freshness 薄适配；不建立统一 `SourceRefreshMiddleware`。
- tool-result micro-compaction 并入 ToolResultBudget，完整 conversation reduction 归 Compaction；tool registry/tool_search、subagent context 构造和 model retry 分别归 Agent runtime、上游 SubAgent adapter 和 Provider SDK。
- Compaction middleware 只占用 `wrap_model_call` seam；archive、summary、boundary 与 checkpoint 由 runtime 以事务方式提交。自动与手动 compact 共用同一引擎。
- LangChain Provider adapter 在唯一出口执行 message/tool schema canonicalization、cache marker 和 Provider capability 适配；Noesis 不增加第二个 canonical request adapter，`PatchToolCalls` 也不被误当成 Provider encoder。
- MCP/tool registry、subagent scheduler、stream/delivery、attachments、HITL host 和 usage 统计继续属于 runtime/service，不塞进 middleware。
- **BREAKING（仅内部 Python import）**：实施时删除 `noesis.agents.middlewares.kernel.*`、旧五 owner、隐式 `ContextVar` 链和手写 inventory；不保留长期 old/new 双轨。
- 不改变 `/api/chat`、现有 SSE 事件名称、assistant 持久化状态机和四个 `qa_type` 的产品职责。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `agent-runtime`: 重写 context source、多阶段减重、compaction、Provider request、file state、tool catalog、subagent context 与观测的可验收行为。
- `agent-harness`: 重写 DeepAgents 风格 factory、Profile 装配、middleware 顺序、inventory、目录依赖和上游契约。
- `agent-tool-failure-handling`: 将 typed failure 与确定性 tool-result replacement 从宏观 `ToolExecutionMiddleware` 分离，保持 status/category/outcome 契约。

## Impact

- 主要影响 `backend/packages/noesis-core/src/noesis/factory.py`、`noesis/agents/middlewares/`、`noesis/runtime/`、Super/Fault/QA Agent 构造、MCP/tool registry、工具包装、context/usage 流式观测与相关测试。
- 研究基线为 Claude Code `2.1.88`、DeepAgents `0.6.12`、LangChain `1.3.15`、LangGraph `1.2.11` 和 DeerFlow `bec62779`；实施前必须 pin 并固定 hook/state 契约。
- 平台 API、`/api/chat` SSE 和数据库 schema 保持兼容。
- 本变更只产出设计与实施任务，不直接修改 runtime 代码。
