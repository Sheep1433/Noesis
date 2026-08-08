## Context

Noesis 当前通过 `backend/packages/harness/noesis/factory.py` 统一装配 LangChain `create_agent`，公共栈包含 `SessionClockMiddleware`、`ModelRetryMiddleware`、`DanglingToolCallMiddleware`、`SummarizationOffloadMiddleware`、`LoopDetectionMiddleware`、`ToolErrorHandlingMiddleware`、LangChain `ToolCallLimitMiddleware`、`ContextBudgetGuardMiddleware` 和 `ContextMetricsMiddleware`。SuperAgent 等 Profile 再加入 Filesystem、SubAgent、Skills、Memory、Todo、Attachments 与 HITL。

当前问题不是 factory 不统一，而是运行状态的 owner 不统一：工具大结果只在整体 context 接近阈值后由 summarization 扫描；context token 在 summary、guard、metrics 多处计算；loop 与 tool limits 分别维护计数；model retry 不表达正常响应中的 length/safety/empty terminal；子 Agent 只有 `task` 调用次数，没有活跃槽位和嵌套深度。

本设计参考 `docs/research/agents/codex-agent-runtime-lessons.md`。Codex 使用显式 turn loop、ContextManager、ToolOrchestrator 和 agent registry 承担关键状态机，而不是为每个异常增加 middleware。Noesis 不拥有 Codex 那样的自研 turn loop，因此继续以 LangChain middleware 作为主要实现与装配方式；借鉴的是职责边界和唯一状态 owner，不是照搬 Codex 的组件形态。

## Goals / Non-Goals

**Goals:**

- 为 model、context、tool 和 run continuation 建立唯一 outcome 与状态 owner。
- 将工具结果在进入 Agent history 时立即有界化。
- 让线上、子 Agent 和离线评测使用同一 runtime kernel。
- 让 SSE、parts、持久化和评测看到一致 stop reason。
- 删除新实现取代的独立 middleware、重复 token 计算和兼容 wrapper。

**Non-Goals:**

- 不移植 DeerFlow 的完整 middleware 集。
- 不改变四个 `qa_type` 的产品职责，也不让 CaseCoordinator 改走 ReAct runtime。
- 不替换 LangChain/LangGraph 或实现 Codex 的完整 Responses turn loop。
- 不在本变更实现 token 来源 attribution；只消费 `add-agent-context-usage-attribution` 提供的实际 usage。
- 不新增通用 ReadBeforeWrite、title、UI progress 或全局输入转义 middleware。

## Decisions

### 1. 公共职责以少量 LangChain Middleware 接入

公共 kernel 定义五个 owner：

| Middleware owner | 内部协作者 | LangChain 接入点 |
|---|---|---|
| `ContextLifecycleMiddleware` | context assembler、normalizer、compaction service | `before_model` / `abefore_model` + innermost `wrap_model_call` / `awrap_model_call` |
| `ModelExecutionMiddleware` | retry classifier、model attempt observer | `wrap_model_call` / `awrap_model_call` |
| `ToolExecutionMiddleware` | typed failure、result envelope、无 backend fallback | `wrap_tool_call` / `awrap_tool_call` |
| `RunGovernorMiddleware` | run-scoped counters、subagent registry adapter | `before_model`、`wrap_tool_call` 及 task spawn hook |
| `RuntimeTelemetryMiddleware` | attribution/event sink | model/tool wrap hook、`after_agent`；只读 lifecycle outcome |

上述 middleware 是 `create_agent` 的权威 lifecycle 接入点。复杂的纯计算、存储和 policy 放在 `noesis/runtime/` service，middleware 负责读取 `ModelRequest` / `ToolCallRequest` / Agent state，调用 service，并通过 LangChain 的 response、`Command`、state update 或 `jump_to` 返回控制结果。`noesis/middlewares/` 不再一项异常一个状态机。

替代方案是直接建立 Noesis 自有完整 turn loop。它能获得更强控制，但会复制 LangGraph ReAct loop、HITL/checkpointer 与 streaming 行为，现阶段成本和分叉风险过高，因此不采用。若某个 stop reason 无法从公开 hook 取得，应先通过 model response metadata/callback 补足观测；只有契约测试证明公开 API 无法表达时，才单独提案薄 runner adapter，本 change SHALL NOT 预先实现第二套 loop。

### 2. 使用统一 RuntimeOutcome

新增内部数据结构，名称在实现时可按项目命名调整，但语义固定：

```text
RuntimeOutcome
  phase: model | context | tool | governor
  status: continue | retry | stop | error
  reason: stable enum
  visible_output_started: bool
  side_effect_started: bool
  retryable: bool
  detail: internal-only
```

`detail` 只进日志/trace，不直接进入 SSE。Delivery 层根据 `status/reason` 映射为现有 `RunEvent`、assistant `finish_reason` 和用户可见短句。这样 model retry、context overflow、governor stop 不再各自抛无法区分的 `RuntimeError`。

Model Execution 继续使用 `noesis/runtime/model_attempt.py` 观察可见 token、tool 与 HITL 边界。只有 `retryable=true`、`visible_output_started=false`、`side_effect_started=false` 时允许重试。Provider 自身 sampling retry 保持关闭，避免双层重试；摘要等非交互模型调用可保留独立 provider retry。

`empty_after_tools` 由 Model Execution 在 `wrap_model_call` 内处理：首次检测到“已有工具结果但模型返回空正文且无新 tool call”时，以仅作用于本次 request、不会写入 conversation state 的收敛提示再次调用 handler；同一 model step 最多追加一次。第二次仍为空时，返回固定的可见 fallback `AIMessage`，并记录 `empty_after_tools` stop outcome。它不是异常重试，不重放工具，不单建 TerminalResponse middleware。

### 3. ContextSnapshot 只构造一次

Context Lifecycle 使用两阶段但单一 owner：`before_model` 负责基于 state 的 call/output normalization 与 compaction；位于 Skills/Memory 等 request transformer 之后的 innermost `wrap_model_call` 负责基于最终 `ModelRequest` 构造 `ContextSnapshot`、最终预算校验和 Provider dispatch。这样 snapshot 才包含最终 system prompt 与 advertised tools。

```text
ContextSnapshot
  normalized_messages
  system/context sources
  advertised_tools
  estimated_tokens
  model_limit
  provenance
```

流程为：

```text
context sources
  → normalize call/output pairs
  → assemble final request
  → estimate once
  → compact when required
  → rebuild sources
  → estimate once more
  → provider or context_exhausted
```

现有 `context_metrics.py` 的 token counter 作为共享 estimator；`SummarizationOffloadMiddleware`、`ContextBudgetGuardMiddleware`、`ContextMetricsMiddleware` 不再分别重新构造口径。Telemetry 读取 snapshot，不参与是否 compact 的判断。

Compaction 仅压缩 conversation history。基础 system prompt、当前 Skills 索引、Memory、SessionClock 与其它可重建 capability context 通过 source provider 重新加入。附件已经属于本轮 HumanMessage 输入，不作为可重建 context source。source provider 写 provenance，与 `add-agent-context-usage-attribution` 共用，不在 prompt 中插入调试分隔符。

### 4. 工具结果在 dispatch 边界形成 Envelope

`ToolErrorHandlingMiddleware` 的 typed exception 分类保留，但迁入统一 Tool Execution。内部 envelope：

```text
ToolResultEnvelope
  tool_call_id / tool_name
  status / category / outcome
  content
  bounded_by: deepagents | noesis_fallback | none
  original_size / omitted_size (仅 Noesis fallback 可取得时)
  timing
```

顺序固定为：

```text
invoke
  → typed failure classification / outcome parsing
  → output bounding
  → ToolMessage + lifecycle event
```

这样有界化不会改变 `status/outcome`。DeepAgents 0.6.7 的 `FilesystemMiddleware.wrap_tool_call/awrap_tool_call` 已经围绕所有未排除的工具结果运行：超过 `tool_token_limit_before_evict` 时写入同一个 backend 的 `large_tool_results` 路径并返回有界文本。因此有 backend 的 Noesis Profile 直接采用这一实现；位于其外层的 `ToolExecutionMiddleware` 将处理后的 ToolMessage content 作为权威结果纳入 envelope，不解析第三方提示文本，也不二次 offload。

COMMON_QA、SimpleMCP 等当前没有 backend，因而不会挂载 `FilesystemMiddleware`。这些 Profile 的未有界结果由 `ToolExecutionMiddleware` 做一次 head/tail fallback，记录原始大小与省略量但不伪造不可读取的 artifact。若后续给这些 Profile 提供 session artifact backend，应通过挂载同一个 `FilesystemMiddleware` 获得 offload，而不是在 Noesis 新建另一套存储协议。

当前 `summary_offload/` 文件只作为迁移期旧数据，不再生成。实现完成后删除 `_offload_tool_results`、`_process_tool_message` 等 summary 内工具职责。

DeerFlow 额外实现 `ToolOutputBudgetMiddleware` 的原因不同：截至调研 commit `bec62779`，它没有采用 DeepAgents `FilesystemMiddleware`，而使用自身 sandbox/file tool 体系；该 middleware 必须覆盖 web、MCP、bash 等任意 ToolMessage，同时处理宿主 thread outputs、远程 sandbox 与历史消息兜底。这个实现只证明“工具边界需要预算”，不构成 Noesis 再实现同类 offload 的依据。

### 4.1 最终 Middleware Inventory

| 类别 | 直接采用 | Noesis 自定义 |
|---|---|---|
| 公共 runtime | 无 | ContextLifecycle、ModelExecution、ToolExecution、RunGovernor、RuntimeTelemetry |
| 文件/子 Agent | DeepAgents Filesystem、SubAgent、AsyncSubAgent | 无 |
| 计划与审批 | LangChain TodoList、HumanInTheLoop | 无 |
| Skills/Memory | DeepAgents Skills/Memory 基类的扫描、加载和 prompt 注入 | VersionedSkills 增加来源版本失效；TurnMemory 每次用户 turn 加载一次 |
| 会话输入 | 无 | AttachmentInputResolver（不是 middleware） |
| compaction | LangChain Summarization 作为内部 engine/父类 | 决策 owner 为 ContextLifecycle |

现有 `SessionClockMiddleware` 迁为 Context Lifecycle 的 context source，不再单独挂载。现有 RevisableSkills 改为 VersionedSkills；`MemoryMiddleware + MemorySyncMiddleware` 收敛为 TurnMemory；ChatAttachments 迁为 input resolver。DanglingToolCall、SummarizationOffload、ContextBudgetGuard、ModelRetry、ToolErrorHandling、LoopDetection、ContextMetrics 及两个 ToolCallLimit 的职责迁移完成后删除；不保留 legacy 开关。

### 4.2 Capability Adapter 收敛

#### Skills

DeepAgents `SkillsMiddleware.before_agent` 在 checkpoint state 存在 `skills_metadata` 时直接跳过目录扫描。Noesis 允许用户在会话存续期间安装、删除或启停 Skills，因此必须有失效信号。

`VersionedSkillsMiddleware` 继承 DeepAgents Skills，并使用现有 `.skills_revision` 作为不透明 revision。它不得复制 DeepAgents 的 scan、parse 或 prompt injection 逻辑；在上游未提供公开 reload hook 时，可以向父类 `before_agent` 传入只省略其缓存字段的最小 state view，并通过 pinned-version 契约测试固定该接缝。Skill frontmatter 解析、override 顺序、warning 与 system prompt 注入仍完全由 DeepAgents 完成。

替代方案是在每轮创建 Agent 时总是重扫。Checkpoint 中的 private state 仍可能覆盖新实例，而且无变化时产生多余 filesystem I/O，因此不采用。

#### Memory

DeepAgents `MemoryMiddleware` 在 checkpoint state 已存在 `memory_contents` 时跳过加载，因此同一 thread 的后续用户 turn 会继续使用首次缓存。旧 `MemorySyncMiddleware` 则在每次 model call 前下载全部来源，使同一次 run 的基础指令发生变化。

`TurnMemoryMiddleware` 继承 DeepAgents Memory，只覆盖 `before_agent/abefore_agent`：每次顶层 Agent invocation 向父类提供不含旧 `memory_contents` 的最小 state view，使父类重新加载一次；本轮后续 model call 始终使用这份固定内容。Agent 或平台在本轮修改 Memory 后，下一次用户 turn 生效，不在同一 run 内重新注入。

Noesis 不建立 `.memory_revision` marker，也不要求 Memory 写入口发送缓存失效通知。Memory 的读取、格式化、HTML comment 处理和 prompt 注入继续使用 DeepAgents 实现。Skills 仍保留 revision，因为 Skills 的安装、删除和启停需要避免无变化时扫描整个目录；两者不强行使用相同缓存策略。

#### Attachments

`ChatAttachmentsMiddleware` 只实现 `abefore_agent`，读取 DB 后替换最后一条 HumanMessage。它既不需要观察后续 model/tool call，也不维护跨调用 state，因此迁为 `noesis.runtime.attachments.AttachmentInputResolver`。

GeneralQAAgent/SuperAgent 在构造 `stream_args.input` 前调用 resolver，得到最终 HumanMessage；resume 输入是 `Command(resume=...)`，不重新注入图片。resolver 继续通过 `noesis.runtime.deps` 使用 attachment service port，保持 harness 不反向 import 平台。附件工具是否挂载仍由 Profile 决定。

### 5. Run Governor 使用 run-scoped state

Governor state 以 `run_id` 为 key，由 runtime context/checkpoint 可恢复字段持有：

```text
model_calls
tool_calls_total / per_tool window
active_subagents / subagents_total / max_depth
actual_provider_tokens (optional)
stop_reason
```

原 `LoopDetectionMiddleware` 的窗口算法与 LangChain `ToolCallLimitMiddleware` 的配置语义迁入 governor adapter，计数源只保留一份。子 Agent spawn 前原子预留槽位，spawn 失败释放；完成/中断后释放 active slot，但 total 不回退。父 run identity 必须由 `noesis/runtime/thread_context.py` 的统一解析路径获得，禁止使用默认字符串让会话共享计数。

累计 token 预算只有收到按 model run id 去重的实际 Provider usage 才启用。context 估算只用于单请求窗口管理。

### 6. Factory 按 Hook 语义显式排序

`factory.py` SHALL 维护一份从 outermost 到 innermost 的实际顺序，而不是简单拼接“capability 后接 kernel”：

```text
RuntimeTelemetry
  → ToolExecution
  → Filesystem / SubAgent / AsyncSubAgent / Todo / VersionedSkills / TurnMemory
  → HITL
  → RunGovernor
  → ContextLifecycle
  → ModelExecution
```

该顺序保证 ToolExecution 在 Filesystem 外层取得 offload 后结果；VersionedSkills/TurnMemory 在 ContextLifecycle 外层完成最终 system request 变换；RunGovernor 的 `after_model` 先于位于其外层的 HITL 执行。LangChain `before_*` 正序、`wrap_*` 嵌套、`after_*` 逆序必须由契约测试固定。`build_subagent_default_middleware` 调用同一个 inventory builder，并注入父 governor scope；不得重新建立独立总预算。

SessionClock 改为 ContextLifecycle 内部 context source；附件由 input resolver 处理；Skills 使用 version-aware adapter，Memory 使用 turn-boundary adapter。三个旧 adapter 名称均不再保留。

### 7. SSE 与持久化只增加兼容字段

RuntimeOutcome 进入共享 stream 核后映射为：

- attempt retry：现有 `run-status=retrying` / `running`；
- 可保留正文的 stop：assistant `partial` + `finish_reason=<stable reason>`；
- 无可用正文且不可恢复错误：现有 `error` 终态 + reason；
- governor stop：保留已有 parts，通常为 `partial`；
- tool output：继续使用现有 `output/status/errorCategory/outcome`；有界 ToolMessage content 按现行桥接规则展示，不增加依赖第三方文案解析的 artifact 字段。

不新增 `/api/chat` 端点。旧前端忽略附加字段仍能依赖现有 `status/output/finish` 收尾。前端后续可按 reason 展示更准确文案，但不在本变更建立一套新的错误 UI。

### 8. 评测验证的是 outcome，不复制 runtime

Harbor、BrowseComp 和 Agentic RAG 继续通过 `noesis.runtime.stream.stream_agent_events`。评测 adapter 只能收集 RuntimeOutcome/RunEvent，不得自行执行 retry、compaction、tool bounding 或 governor。

新增确定性 fixture：超大 ToolMessage、dangling call、模型 length stop、可见 token 后断流、工具后空 response、重复工具、并发 subagent。真实模型 E2E 只保留一条代表性场景，不作为所有边界测试的唯一反馈。

## Risks / Trade-offs

- [LangChain hook 无法观察完整 finish reason] → 先写 provider/astream_events 契约测试；不足时增加薄 runner adapter，不复制 ReAct loop。
- [合并 middleware 时改变 wrap 顺序] → 为实际 hook 进入/退出顺序写契约测试，再逐项迁移；不一次性重命名后依赖全量 E2E 排查。
- [DeepAgents offload 文案变化] → 不解析其提示文本；只把处理后的 ToolMessage content 当作权威结果，并以 pinned-version 契约测试发现行为变化。
- [Governor checkpoint 与活跃子 Agent 状态不一致] → slot reservation 使用原子状态更新；恢复时以实际 registry/任务状态 reconcile。
- [与 token attribution change 并行冲突] → 本 change 只定义消费接口；先合并 attribution 数据结构或用 optional adapter，禁止建立第二套 usage collector。

## Migration Plan

1. 增加 outcome/envelope/governor 数据结构与契约测试，现有 middleware 暂时适配这些结构。
2. 直接采用 Filesystem 的 offload，并在无 backend 路径实现 Tool Execution fallback；从 summary 中删除 tool offload 职责。
3. 将 ModelRetry 迁入 Model Execution，并加入 finish reason/empty outcome；保持现有重试配置键兼容。
4. 将 loop、tool limit、subagent limit 迁入 Governor；迁移完成后删除旧状态 registry 与重复 middleware。
5. 重构 Context Lifecycle 和 source reinjection；替代 dangling/summary/context guard 的独立决策。
6. 接入 RuntimeTelemetry 与 SSE/parts/持久化映射，再更新评测 collector。
7. 删除旧类、旧配置和不可达分支，运行 harness、stream、Harbor 及关键 Agent 回归测试。

回滚以提交为单位，不保留运行时 feature flag 双轨。每个阶段在删除旧 owner 前必须具备相同行为的契约测试；若阶段失败，回滚该阶段代码而不是长期并行两套 runtime。

## Open Questions

无。
