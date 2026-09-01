# 决策：上下文中间件收敛：Claude Code 的 context 是 pipeline 不是 summarizer

状态：implemented
日期：2026-08-11
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 旧五个 kernel middleware 靠 `ContextVar` 在 hook 间传隐式状态（ToolExecution 顺手管 governor、ContextLifecycle 顺手注入时钟、telemetry 再读全局快照），类名宏观但控制流隐蔽，跨 10+ 文件才追得完；目标是「DeepAgents 风格一次装配 + Claude Code 式上下文/重试策略 + Noesis delivery 稳定」。

**研究基线（均 2026-08-11 只读）：** `@anthropic-ai/claude-code@2.1.88`（提取仓 `d907d498`）、DeepAgents `0.6.12`、LangChain、DeerFlow。

**关键结论：**
- Claude Code 2.1.88 的上下文管理是**一条显式 runtime pipeline**，不是单个 summarization middleware：canonical request → 分层减重 → safe compaction transaction → summary PTL retry → failure breaker → 压缩后权威状态重建 → tool/MCP schema 管理 → subagent 隔离/fork → usage/cache 观测。Noesis 若「行为几乎一致」必须整套具备，只补 auto compact 不够。
- 现有 `ContextLifecycle` 捕获 compaction 异常后继续运行，并用本地近似 token 估算直接伪造模型答复——偏离 Claude Code 的「预留摘要空间 + 明确失败恢复」，且绕开 Provider 真实语义；重构必须消除这类隐式控制流。
- DeepAgents 0.6.12 已覆盖 context strategy 大部分基础能力（走 `create_deep_agent()` → LangChain `create_agent()` 装 middleware，`recursion_limit=9999`），但不能原样承担完整 compaction policy：Summarization 加窄策略层、自行控制 middleware 顺序。
- **保留的 Noesis middleware 收敛为 4 个**：`ToolFailureMiddleware`、`ToolResultLimitMiddleware`、`CompactionMiddleware`、`SafeModelRetryMiddleware`。其中 SafeModelRetry 必须自研：已安装的 LangChain `ModelRetryMiddleware` 不知道 SSE 是否已产生可见 token，也不知道工具副作用边界，异常后可能重放 model step；除非把安全判断迁到更合适的 provider/stream seam，否则不能直接用原生 retry。
- `factory.py` 同时维护真实 stack 和手写 inventory，两者已分叉；重构时删旧装配，只保留单一装配点。

**How to apply：**
- 设计落地：`Interview/highlights/Middleware/agent-context-middleware-boundaries.md`（研究/评审，归档于知识库，实现稳定后回归 `docs/research/agents/`）；`openspec/changes/simplify-agent-context-architecture/`（proposal/design/specs/tasks，3 份 delta spec：行为化描述移除具体类名、subagent 默认隔离仅接收明确任务输入、remote trust 移出本 change）。
- 实现前先对齐 outer-to-inner 目标顺序与 invariants；取消语义必须可终止；SafeModelRetry 沿用已验证 request、attempt 单独计数，只有改变 messages 才走完整 lifecycle。

**验证与遗留：** 实现未开始（待单独 session）；OpenSpec 复审已通过。参考：`Knowledge/Claude-Code/Claude-Code-记忆机制.md`。
