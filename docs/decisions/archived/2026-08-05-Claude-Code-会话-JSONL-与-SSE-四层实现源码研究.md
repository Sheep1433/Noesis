# 决策：Claude Code 会话 JSONL 与 SSE 四层实现（源码研究）

状态：implemented
日期：2026-08-05
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** Noesis 数据结构以 Claude Code 为蓝本，且要做多端 SSE 一致；源码在 `cloud-code/claude-code-source/src/`（v2.1.88 还原）。

**How to apply：**
- 持久化：transcript JSONL per project（`types/logs.ts` 的 `Entry` 约 20 类）+ parentUuid DAG（并行 tool_use 兄弟分支恢复）+ 多类 snapshot（file history/attribution/content replacement/context-collapse「marble-origami」）+ metadata + 远端 CCR v2；fork 用 `parentSessionId` 引用非复制全量。`sessionStorage.ts` 是核心，`getTranscriptPath`/`getAgentTranscriptPath` 决定落盘位置。
- subagent：`isSidechain:true` 事件写在主 session 目录下 `subagents/agent-<agentId>.jsonl`，同名 `.meta.json` sidecar 存 agentType/worktreePath 供 resume 路由（`sessionStorage.ts:247-258`）；主 jsonl 只留 tool_use/result 引用。
- compact：压缩标记字段是 `isCompactSummary:true`（`sessionStoragePortable.ts:150`）；grep 命中该词 ≠ 真实事件（本地所有 jsonl 命中均为「会话内容讨论到它」）。`CLAUDE_CODE_AUTO_COMPACT_WINDOW` 把本地计算的有效窗口取 `Math.min(原窗口, 值)`（`autoCompact.ts:38-46`），auto-compact 阈值 ≈ 窗口 − 20k 预留 − 13k buffer；只影响本地阈值，不改真实 API 请求。
- SSE 四层：`src/cli/transports/SSETransport.ts`（传输层长连接）、`src/services/api/claude.ts`（`stream:true` 流式调用）、`src/utils/stream.ts`（`Stream<T>` 异步队列原语）、`src/services/tools/StreamingToolExecutor.ts`（工具边流式到达边执行）。
- Tool 模型：`types/logs.ts` 之外看 `Tool.ts` 的 `Tool` 接口（isReadOnly/isDestructive/isConcurrencySafe/interruptBehavior/maxResultSizeChars/checkPermissions/toAutoClassifierInput）与 `ToolUseContext`（contentReplacementState、renderedSystemPrompt 共享父 prompt 字节保 cache、queryTracking{chainId,depth}）。
- 方法论：研究会话事件结构先做「字段名确认 → 全盘扫描 → 区分真实事件 vs 内容提及」，别被关键词 grep 误导；制造特殊场景用「批次触发 + 清理动作本身也产生事件」（TaskStop 返回已完成也是有效 tool_result 事件）。
