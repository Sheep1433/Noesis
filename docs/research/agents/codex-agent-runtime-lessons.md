# Codex Agent Runtime 对 Noesis 的启示

> 状态：Research  
> 调研日期：2026-08-04  
> 外部版本：OpenAI Codex `d4fb78bfc59009a2bbc3245d125bf8ba92a8e33e`  
> 关联 OpenSpec：`add-agent-context-usage-attribution`

## 1. 调研目标与范围

本报告不复制 Codex 的 coding 场景，也不按 DeerFlow 的 middleware 清单逐项移植。目标是回答：Noesis 作为通用 Agent harness，哪些运行时职责必须统一，哪些应保留为场景能力，哪些不应该做成 middleware。

调研范围包括 Codex 的 turn loop、context manager、compaction、tool orchestrator、sandbox/approval、Skills 和 subagent registry；DeerFlow 仅用于发现候选问题，再回到 Codex 源码确认成熟实现采用了什么边界。

## 2. Noesis 现状

### 2.1 当前装配

`create_noesis_agent` 当前把三类对象放在同一列表：

1. 能力：Filesystem、SubAgent、Skills、Memory、Attachments、Todo、HITL。
2. 运行时防护：ModelRetry、DanglingToolCall、SummarizationOffload、LoopDetection、ToolErrorHandling、ToolCallLimit、ContextBudgetGuard。
3. 运行信息：SessionClock、ContextMetrics。

这套实现已经由统一 factory 装配，不存在各 Agent 自建完整运行栈的问题。但职责仍有交叉：

- `SummarizationOffloadMiddleware` 同时负责大工具结果转存、上下文阈值判断、历史切分和摘要。
- `ContextBudgetGuardMiddleware` 与摘要中间件分别计算最终请求预算。
- `LoopDetectionMiddleware` 与两个 `ToolCallLimitMiddleware` 都在治理 run 是否继续。
- `ModelRetryMiddleware` 只覆盖可重试异常，不负责模型正常返回但无法继续的终止状态。
- capability middleware 与 runtime guard 混排，名称数量会随场景增加，但不都属于 harness 内核。

### 2.2 当前真正缺失的能力

- 工具输出在写入 history 时立即进行有界化，避免单次结果在下一次模型请求前才被动处理。
- 模型调用的统一结果分类：成功、可重试传输错误、长度终止、安全终止、已有可见输出后的失败、工具后空响应。
- run 级统一预算：模型调用、工具调用、子 Agent 并发/总量、累计 token 和循环，而不只是多个独立计数器。
- Skills/tool 权限在执行入口校验；仅减少 prompt 中的可见工具不构成权限控制。
- context compaction 后，明确重建稳定上下文与瞬时上下文，避免把旧的动态提示永久写回历史。

## 3. Codex 源码事实

### 3.1 核心不是 middleware chain，而是显式 runtime

Codex 的主循环位于 `session/turn.rs`。每个 sampling step 会捕获同一份 `StepContext`，用它构造 context、公布 tools 并执行 tool calls。模型、上下文和工具不会分别从不一致的 runtime 位置读取会话状态。

它把关键职责分到以下组件：

| 组件 | 职责 |
|---|---|
| turn loop | sampling、tool continuation、steer、错误终态和自动 compaction 的状态推进 |
| ContextManager | 保存 history、构造 prompt、规范化 call/output 配对、估算 token |
| compaction lifecycle | 自动/手动压缩、压缩事件、初始上下文重新注入、模型切换兼容 |
| ToolOrchestrator | approval → sandbox → execution → 有条件升级/重试 |
| ToolRouter / registry | 工具发现、路由、并行策略和统一 dispatch |
| agent registry | 子 Agent 注册、状态、并发槽位和 spawn depth |
| hook runtime | 在明确生命周期点提供扩展，不承担主状态机 |

这说明可靠性逻辑需要有唯一所有者。middleware 可以接入 LangChain hook，但不应成为互相猜测顺序的微型状态机集合。

### 3.2 Context 是可重建的状态，不只是消息摘要

Codex 的 `ContextManager` 在每次生成 prompt 前执行 normalization：

- 缺失 tool output 时补稳定 ID 的 synthetic output；
- 删除没有对应 call 的 orphan output；
- 按模型 modality 删除不支持的图片或音频；
- 对 function output 使用统一 truncation policy；
- 同时维护 provider usage 与本地估算。

Compaction 是显式生命周期事件。压缩后 replacement history 与 reference context 一起更新；手动/turn 前压缩会让下一轮重新注入初始 context，turn 中压缩则在最后一条真实 user message 前注入稳定 context。它还处理模型切换造成的 context window 或 compaction compatibility 变化。

对 Noesis 的意义是：tool-call repair、摘要和静态/动态上下文重建属于同一个 Context Lifecycle，而不是三个互不知情的补丁。

### 3.3 工具输出在边界有界化

Codex 同时在两个位置限制输出：

- unified exec 使用 `HeadTailBuffer`，保留头尾、丢弃中间，并显式记录省略字节；
- ContextManager 在 history 序列化时按统一 policy 截断 function output。

这与“上下文达到 75% 后才扫描旧 ToolMessage”不同。大输出首先在工具边界变成有界结果，compaction 再处理跨多轮历史。

### 3.4 模型重试属于 sampling 生命周期

`responses_retry.rs` 只处理被判定为 retryable 的 Responses stream 错误，使用 provider retry delay 或 backoff，并向 UI 发送 reconnect 状态；重试耗尽后还可从 WebSocket 切换到 HTTPS。重试发生在 sampling request loop 内，因此它知道当前请求、transport 和已经产生的事件，而不是捕获任意异常后重放整个 Agent step。

Noesis 不需要照搬 transport fallback，但需要保留现有“产生可见 token 或进入 tool/HITL 后不重试”的边界，并把模型终止原因放到同一 model execution owner 中。

### 3.5 安全控制在工具执行入口

Codex 的 `ToolOrchestrator` 固定执行 approval、sandbox 选择、首次尝试、sandbox denial 判断和经授权的升级重试。approval 可以由 hook、自动 reviewer 或用户给出，结果与 tool call 绑定。

这不是 prompt sanitization，也不是在 tool result 返回后补救。Skills、MCP 或动态 tools 最终都必须经过执行入口的权限与 sandbox 规则。

### 3.6 子 Agent 使用 registry 治理

Codex 在 agent registry 预留 spawn slot，并限制总线程数和 spawn depth。子 Agent 的生命周期、状态与父子关系是运行时数据，不依赖模型是否记得自己已经委派过。

Noesis 当前 `task` 调用次数限制只能覆盖部分问题，不能表达并发槽位、嵌套深度和活跃子 Agent 生命周期。

### 3.7 Skills 是上下文来源，不是无限扩张的 middleware

Codex 将 Skills 拆为发现、配置 policy、显式/隐式 invocation、按需 injection 和 telemetry。它不会为每个 Skill 建立一条新的运行时防护链。Skill 是否可用与工具是否获准执行是两个不同问题。

## 4. DeerFlow 候选问题在 Codex 中的落点

| DeerFlow 暴露的问题 | Codex 的处理边界 | Noesis 应采用的方向 |
|---|---|---|
| ToolOutputBudget | 工具 buffer + history serialization | 工具结果入口统一有界化，不新增独立后置扫描器 |
| DanglingToolCall | ContextManager normalization | 纳入 Context Lifecycle |
| Summarization / DurableContext | compaction checkpoint + context reinjection | 压缩后重建稳定上下文，不复制 delegation 文本 middleware |
| Model retry / length / safety / empty terminal | sampling turn loop | 合并为 Model Execution 状态，不建四个 middleware |
| ReadBeforeWrite | tool runtime / patch protocol / approval | 如文件工具需要，做工具契约或 optimistic version，不做全局 middleware |
| SkillToolPolicy | tool exposure + execution policy | 可见性过滤与执行期授权分开 |
| SubagentLimit | agent registry | 建立统一 delegation governor |
| TokenBudget / LoopDetection / ToolCallLimit | turn/run 状态 | 合并为 Run Governor |
| Input/ToolResultSanitization | 信任边界和结构化协议 | 只处理已确认的控制标记；不做全局文本转义 |
| SystemMessageCoalescing | context assembly | 从来源结构化组装，不事后合并字符串 |

## 5. Noesis 推荐边界

### 5.1 Harness 内核只保留五个运行时职责

这里的名称描述职责，不强制每项都实现成一个 LangChain middleware 文件。

| 内核职责 | 现有实现 | 调整建议 |
|---|---|---|
| `ContextLifecycle` | DanglingToolCall + SummarizationOffload + ContextBudgetGuard | 合并 token 预算判断、history normalization、compaction 和稳定 context 重建；工具大结果处理移出 |
| `ModelExecution` | ModelRetry | 增加 stop reason、空终态、部分输出边界；由一次 model call 的统一结果类型驱动 |
| `ToolExecution` | ToolErrorHandling | 增加 output envelope、大小预算、执行期 policy；sandbox/HITL 仍使用专门执行设施 |
| `RunGovernor` | LoopDetection + ToolCallLimit | 统一 model/tool/subagent/token budget、循环判断与 stop reason；计数只维护一份 |
| `RuntimeTelemetry` | ContextMetrics | 按现有 attribution spec 汇总 model/tool/subagent/compaction 事件；SessionClock 不属于 telemetry |

`SessionClock` 可以继续作为很薄的 context source，或并入统一的 context assembly；它不应被描述为运行时 guard。

### 5.2 场景能力继续独立，但不计入“公共中间件数量”

- Filesystem、SubAgent、AsyncSubAgent
- Skills、Memory、Todo
- ChatAttachments
- HITL / ask-user
- RAG、MCP 和其它工具 provider

这些能力由 Agent Profile 选择。它们不应该全部塞进 `build_noesis_runtime_middleware()`，也不应该为了目录统一而合并成一个大类。

### 5.3 建议新增的能力

按优先级排序：

1. **Tool result envelope 与有界输出**：每个工具返回 `status/category/content/artifact/preview/size`；超限内容写 artifact，history 只保留摘要、头尾或引用。
2. **Model execution outcome**：统一表达 retryable transport error、context overflow、length stop、safety stop、partial output 和 empty-after-tools。
3. **Run Governor**：先统一现有 loop/tool counters，再增加 subagent concurrent/total/depth；累计 token 等 attribution 完成后接入。
4. **执行期 tool policy**：按 Agent Profile、Skill、用户授权和 tool risk 校验，不能只靠 prompt 可见性。
5. **Compaction checkpoint**：验证 summary 后 Skills、Memory、任务目标、未完成 tool state 是否能从 source 重建；只保存必要的 durable state。

### 5.4 不建议新增

- 不新增与 DeerFlow 同名的 20 多个 middleware。
- 不增加通用 `ReadBeforeWriteMiddleware`；版本校验属于可写文件工具协议。
- 不增加全局 HTML/XML escape；RAG、Web、MCP 分别声明信任边界并处理框架控制标记。
- 不增加 `TerminalResponseMiddleware` 作为补丁；空终态应由 model/turn lifecycle 识别并产生稳定 outcome。
- 不用 prompt 中的 delegation ledger 代替真实 subagent registry。
- 不把 title、UI progress、上传管理放入 harness runtime guard。

## 6. 推荐实施顺序

1. 先补 runtime outcome 数据结构和顺序契约测试，不改行为。
2. 把工具结果有界化从 `SummarizationOffloadMiddleware` 移到 tool execution boundary。
3. 合并 ModelRetry 与模型终止状态，打通 SSE stop reason。
4. 合并 loop/tool/subagent 预算为 Run Governor。
5. 重构 Context Lifecycle，最后删除被替代的独立 middleware 与重复 token 计算。
6. 在 `add-agent-context-usage-attribution` 完成后接入累计 token budget。

这应作为新的 OpenSpec change 实施。不要直接追加到 token attribution change；后者只负责可观测性，不能承担 runtime 重构。

## 7. 待验证问题

- LangChain `create_agent` 的 middleware hook 是否足以让 `ModelExecution` 返回完整 stop reason；不足时是否需要一层 Noesis turn runner。
- DeepAgents filesystem tool 的返回边界能否统一包装，还是需要在 tool registry/router 层完成。
- 当前 summary 后 Skills、Memory、附件和 task 目标分别由何处重注入，哪些已经可靠、哪些仅偶然保留在历史中。
- Harbor 与线上 runtime 是否都能消费同一个 Run Governor outcome，而不增加评测 adapter 分支。

## 8. 资料来源

- OpenAI Codex 源码，commit `d4fb78bfc59009a2bbc3245d125bf8ba92a8e33e`：`codex-rs/core/src/session/turn.rs`
- OpenAI Codex：`codex-rs/core/src/context_manager/history.rs`、`normalize.rs`
- OpenAI Codex：`codex-rs/core/src/compact.rs`、`compact_token_budget.rs`
- OpenAI Codex：`codex-rs/core/src/responses_retry.rs`
- OpenAI Codex：`codex-rs/core/src/tools/orchestrator.rs`、`approvals.rs`、`parallel.rs`
- OpenAI Codex：`codex-rs/core/src/unified_exec/head_tail_buffer.rs`
- OpenAI Codex：`codex-rs/core/src/agent/registry.rs`
- OpenAI Codex：`codex-rs/core/src/skills.rs`
- DeerFlow 源码，commit `bec6277930d6ee73c58156689f1556780724e35d`，仅用作候选问题输入。
