# Noesis Agent Runtime 设计导读

> 状态：Proposed  
> 权威设计：[`simplify-agent-context-architecture/design.md`](../../../openspec/changes/simplify-agent-context-architecture/design.md)  
> 可验收行为：[`specs/`](../../../openspec/changes/simplify-agent-context-architecture/specs/)  
> 实施任务：[`tasks.md`](../../../openspec/changes/simplify-agent-context-architecture/tasks.md)  
> 研究依据：[`Agent 上下文管理与 Middleware 边界研究`](/Users/zzq/Library/Mobile%20Documents/iCloud~md~obsidian/Documents/knowledge-base/Interview/highlights/Middleware/agent-context-middleware-boundaries.md)（实现稳定后归档回 `docs/research/agents/`）

## 结论

目标不是“只保留少数 middleware”，而是：

- 上下文管理行为尽量接近 Claude Code 2.1.88；
- 代码结构保持 DeepAgents 风格：一个 factory、平铺 `middleware/`、直接参数装配；
- LangChain/DeepAgents 已有能力只在契约相同时直接使用；缺失时实现 Noesis middleware；
- 不引入 `ContextCompiler`、多层 kernel、可执行 spec 或第二套 Agent loop。

## 目标结构

```text
noesis/
├── factory.py                  # 唯一 ReAct Agent 装配入口
├── middleware/                # 平铺；只放 Noesis 差异能力
│   ├── source_refresh.py
│   ├── dynamic_context.py
│   ├── durable_context.py
│   ├── file_context.py
│   ├── snip.py
│   ├── micro_compaction.py
│   ├── compaction.py
│   ├── tool_catalog.py
│   ├── subagents.py
│   ├── tool_failure.py
│   ├── tool_result_budget.py
│   └── safe_model_retry.py
├── backends/                   # workspace、artifact、archive adapter
├── agents/                     # 场景 prompt、tools 与入口
└── runtime/
    ├── compaction.py          # archive/summary/boundary/checkpoint 事务
    ├── tool_registry.py        # MCP/tool 权威源
    ├── providers/              # canonical request 适配
    └── ...                     # stream、HITL、task registry、attachments
```

DeepAgents/LangChain 的 Filesystem、Skills、Memory、Todo、PatchToolCalls、HITL 和 call-limit middleware 从依赖直接导入，不在 Noesis 内再包一层同义 wrapper。

## Middleware 分工

| Middleware | 主要职责 | 为什么不能只用上游 |
|---|---|---|
| SourceRefresh | 顶层 turn 的 source revision 与定向缓存失效 | DeepAgents Skills/Memory 默认只加载一次 |
| DynamicContext | 注入当前时间、workspace、session、附件索引 | 场景动态来源不属于摘要 |
| DurableContext | 保存 plan/task/skill/file/tool 引用 | 压缩后必须从摘要外恢复 |
| FileContext | read state、stale 提示、写前契约、关键 excerpt 恢复 | DeepAgents Filesystem 不等于 Claude Code 文件状态 |
| Snip | 定点修改 effective history projection | 上游没有等价的持久化 snip record |
| MicroCompaction | 无模型局部减重 | Summarization 不覆盖完整的 Claude 局部阶段 |
| ToolCatalog | deferred MCP/tool schema 与 `tool_search` | 大工具目录不应全量进入每次 request |
| Compaction | 自动/增量/手动压缩、PTL recovery、breaker | DeepAgents 缺 summary reserve、完整 PTL 策略和 durable breaker |
| NoesisSubAgent | isolated/fork/resume 的 context 边界 | DeepAgents 默认 task 语义不是完整 Claude fork |
| ToolFailure | typed 异常转换 | Noesis 已有 status/category/outcome 产品契约 |
| ToolResultBudget | 确定性 replacement 与 artifact record | 需要 resume 后决策一致 |
| SafeModelRetry | 无可见输出/副作用才 retry | LangChain 通用 retry 不理解 Noesis SSE 边界 |

这些是“窄 middleware”，不是十一个平台 owner。每个类只占一个 LangChain lifecycle seam；数据库、MCP 连接、文件 I/O、task 调度和 stream 仍在 runtime/backend/service。

## 请求管道

```text
stable sources / source revision
→ ToolResult replacement
→ Snip projection
→ MicroCompaction
→ ToolCatalog filtering
→ PatchToolCalls
→ auto/reactive Compaction
→ Provider canonicalization + cache marker
→ model call
```

Compaction 只处理 conversation。场景 prompt、Skills、Memory、plan/task、file refs 和 tool catalog 等稳定来源在 compact 后重建，不要求 summary model 记住。

Provider 发送前只有一份 canonical request。本地 token estimate 必须覆盖 system、messages、tool results、tool schemas、attachments 和 framing；Provider actual usage 另行记录，不混为一个数。

## Factory 与顺序

`create_noesis_agent()` 直接接收 `model / tools / system_prompt / middleware / backend / subagents / skills / memory / interrupt_on / checkpointer / state_schema / context_policy / name`。Factory 根据 Profile 参数一次构造 stack，调用方不得在返回后 append。

目标 outer-to-inner 顺序由 OpenSpec design 维护。顺序的关键约束是：

- ToolResultBudget 要看到成功与失败的最终 ToolMessage；
- SourceRefresh 要在 Skills/Memory 读取 private cache 前执行；
- 稳定来源、ToolCatalog 和 PatchToolCalls 要位于 Compaction 外层，让预算看到最终 request；
- SafeModelRetry 位于 Compaction 内层，context overflow 必须返回 Compaction；
- HITL 只拦截工具执行，ToolFailure 不得吞掉 interrupt 和取消。

## 不放在 Middleware 的能力

| 能力 | Owner |
|---|---|
| 用户消息与附件解析 | Service/input resolver |
| MCP 连接、schema 权威源、执行授权 | runtime tool registry |
| archive/summary/boundary/checkpoint 原子提交 | runtime compaction service |
| read/write 真实 I/O 与 mtime/hash 拒绝 | backend/tool adapter |
| subagent 并发、取消、超时、admission | runtime task registry |
| Provider message/schema 最终编码 | provider request adapter |
| usage、finish/stop reason、SSE、assistant 落库 | stream/delivery/service |

## 当前状态

设计尚未实施，当前代码仍使用五个 runtime owner。实施顺序和验收条件以 OpenSpec 为准；实施完成后本文改为 Current 并补真实代码路径。
