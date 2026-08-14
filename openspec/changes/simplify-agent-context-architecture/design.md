## Context

Noesis 当前通过 `factory.py` 调用 LangChain `create_agent()`，并把运行行为集中到五个宏观 middleware：

```text
RuntimeTelemetry → ToolExecution → RunGovernor → ContextLifecycle → ModelExecution
```

这套结构的问题不是 middleware 数量，而是职责与生命周期不一致：prompt、message repair、compaction、工具结果、retry、预算和观测通过多个 hook 与 `ContextVar` 相互影响。Super task-worker 还会在 factory 返回完整 stack 后追加 Skills，手写 inventory 与真实顺序已经分叉。

本设计重新确定两个独立目标：

1. **上下文行为以 Claude Code 2.1.88 为基线。** Noesis 应具有近似的稳定来源装配、局部减重、自动/手动压缩、失败恢复、工具 schema 控制、文件状态、子 Agent 隔离和可观测性。Noesis 当前没有的能力也应纳入目标。
2. **装配方式参考 DeepAgents，运行时归属参考 DeerFlow。** 保持一个 factory/graph 装配入口与直接构造参数；middleware 在 `agents/middlewares/` 内平铺，Agent backend 保持在 `agents/backends/`；不引入 context compiler、可执行 spec 或多层 kernel 目录。

LangChain/DeepAgents 只有在行为契约满足 Claude Code 目标时才直接采用。行为不同或缺失时，Noesis 实现新的窄 middleware；不为了减少自定义类而降低目标能力。

研究基线：Claude Code npm `2.1.88` 与本地提取源码、DeepAgents `0.6.12`、LangChain `1.3.15`、LangGraph `1.2.11`、DeerFlow commit `bec62779`。该组合由 `deepagents==0.6.12` 的 `requires_dist`（`langchain>=1.3.11,<2.0.0`、`langchain-core>=1.4.8`）推导；不得 pin 与之互斥的更低版本。

## Goals / Non-Goals

**Goals:**

- 每次 Provider 调用只有一份经过稳定来源注入、工具 schema 筛选、message repair、micro-compaction 和预算检查的 canonical request。
- 实现 Claude Code 式多阶段 context pressure 处理：局部减重 → 自动压缩 → reactive overflow recovery → 有界失败终止。
- 压缩后恢复文件、Skills、Memory、plan/task、tool/MCP 等稳定来源，不依赖摘要模型记住它们。
- 支持 summary reserve、瞬时 buffer、结构化摘要、summary PTL retry、连续失败熔断和手动 compact。
- 支持 read-file state、stale 检测、写前校验和压缩后的关键文件恢复。
- 支持大工具目录的 deferred schema 与 tool search，避免 MCP/tool definitions 无上限占用 context。
- 子 Agent 默认隔离，只有显式 fork 才继承允许的父上下文。
- 目录和 factory API 保持 DeepAgents 风格，公共包不反向导入场景 Agent。
- 保持 `/api/chat` SSE 与 assistant 持久化状态机兼容。

**Non-Goals:**

- 不复制 Claude Code 的 TypeScript `queryLoop()`、UI、feature flag 名称和固定 token 常量。
- 不保证实现 Anthropic 私有 cache-editing API；Provider 支持时通过 adapter 使用，其他 Provider 使用本地 micro-compaction。
- 不复制 DeerFlow 全部平台 middleware 或任意 Python 类路径扩展。
- 不重写 LangChain/LangGraph ReAct loop。
- 不保留 old/new runtime 长期双轨。

## Decisions

### 1. Agent runtime 包采用 DeerFlow 归属，装配采用 DeepAgents 风格

```text
noesis/
├── factory.py                     # 唯一 ReAct Agent 装配入口
├── agents/                        # 完整 Agent runtime 包
│   ├── __init__.py                # lazy 导出场景 Agent
│   ├── middlewares/
│   │   ├── dynamic_context_middleware.py
│   │   ├── durable_context_middleware.py
│   │   ├── refreshing_skills_middleware.py
│   │   ├── refreshing_memory_middleware.py
│   │   ├── read_before_write_middleware.py
│   │   ├── snip_middleware.py
│   │   ├── compaction_middleware.py
│   │   ├── deferred_tool_filter_middleware.py
│   │   ├── tool_failure_middleware.py
│   │   └── tool_result_budget_middleware.py
│   ├── backends/                  # workspace、archive、artifact adapter
│   ├── runtime/
│   │   └── tool_registry.py       # tool schema、revision、promotion 与权限权威源
│   └── ...                        # 场景 prompt、tools 与 Agent 入口
└── runtime/                       # 平台 streaming、attachments、HITL host、日志
```

DeepAgents/LangChain 的公开 middleware 直接从依赖导入，不复制到 Noesis。Skills/Memory freshness 适配器虽然继承上游实现，也直接平铺在 `agents/middlewares/`，不再增加 `capabilities/` 层级。每个通用 middleware 自包含：只依赖 factory 注入的依赖和 LangGraph typed/private state，不在运行时调用 service、factory 或具体场景模块。

`factory.py` 可以导入 `agents.middlewares`、`agents.backends` 与 `runtime`。场景模块可以按需导入 factory；`agents.__init__` 必须保持 lazy 场景导出，不能 eager import 场景实现。循环依赖通过 import 方向约束解决，而不是把 middleware 移出 Agent runtime 包。

### 2. Factory 直接接收 DeepAgents 风格参数，底层仍调用 LangChain `create_agent`

```python
create_noesis_agent(
    *,
    model,
    tools,
    system_prompt,
    middleware=(),
    backend=None,
    subagents=None,
    skills=None,
    memory=None,
    attachments=None,
    interrupt_on=None,
    checkpointer=None,
    state_schema=None,
    context_policy=None,
    name=None,
)
```

底层装配入口保持现状的 LangChain `create_agent()`，不切换到 `create_deep_agent()`。`subagents`、`skills`、`memory`、`backend`、`interrupt_on` 等参数**不是透传给 `create_deep_agent`**，而是由 factory 内部映射为对应中间件实例并加入 `middleware` 列表，再调用 `create_agent(model=..., tools=..., system_prompt=..., middleware=..., checkpointer=..., state_schema=..., name=...)`：

| 参数 | 映射为 |
|---|---|
| `skills` | `RefreshingSkillsMiddleware`（继承 DeepAgents Skills） |
| `memory` | `RefreshingMemoryMiddleware`（继承 DeepAgents Memory） |
| `subagents` | `SubAgentMiddleware` / `AsyncSubAgentMiddleware`（DeepAgents） |
| `backend` | `FilesystemMiddleware(backend=backend)`（DeepAgents） |
| `interrupt_on` | `HumanInTheLoopMiddleware`（LangChain） |

参数签名采用 DeepAgents 风格仅为统一调用方体验。Factory 一次性完成映射与 stack 构造；调用方不得在 factory 返回后继续 append capability，诊断 inventory 从实际实例列表生成，不维护独立 allowlist。

现有 `COMMON_QA / SUPER_AGENT_QA / FAULT_OPERATION_QA / SimpleMCP` 名称继续用于配置和观测，不新增另一套 Profile class。场景 Agent 负责准备参数。主 Agent、子 Agent 与离线评测都使用该入口。

### 3. 目标 Middleware Stack

Outer-to-inner 模板如下，可选项省略时不改变其余相对顺序：

```text
ToolResultBudgetMiddleware                # Noesis
→ ToolFailureMiddleware                   # Noesis
→ ReadBeforeWriteMiddleware               # Noesis，filesystem Profile
→ TodoListMiddleware                      # LangChain，可选
→ RefreshingSkillsMiddleware              # DeepAgents 薄适配，可选
→ FilesystemMiddleware                    # DeepAgents，可选
→ SubAgentMiddleware                      # DeepAgents；private_state_keys 控制默认隔离
→ RefreshingMemoryMiddleware              # DeepAgents 薄适配，可选
→ DynamicContextMiddleware                # Noesis
→ DurableContextMiddleware                # Noesis
→ SnipMiddleware                          # Noesis
→ DeferredToolFilterMiddleware            # Noesis，工具目录达到阈值时启用
→ PatchToolCallsMiddleware                # DeepAgents
→ CompactionMiddleware                    # Noesis，组合 DeepAgents engine
→ configured ModelCallLimitMiddleware     # LangChain，可选
→ configured ToolCallLimitMiddleware      # LangChain，可选
→ Provider PromptCachingMiddleware        # 上游/provider adapter，可选
→ HumanInTheLoopMiddleware                # LangChain，可选
→ Provider
```

关键顺序：

- ToolResultBudget 位于 ToolFailure 外层，最终 error ToolMessage 也必须有界。
- ReadBeforeWrite 位于 Filesystem 外层，能够观察 read/write/edit 结果以及 backend 异常。
- Skills、Memory、DynamicContext、DurableContext、DeferredToolFilter 和 PatchToolCalls 都位于 Compaction 外层，使 Compaction 看到最终 system/messages/tools。
- ToolResultBudget 先执行不调用模型的 Tool Result 减重，Compaction 再判断是否需要摘要。
- 瞬时 model retry 位于 Provider SDK/adapter；`ContextOverflowError` 返回 Compaction，不由普通 retry 捕获。
- HITL 只影响工具执行。ToolFailure 必须放过 LangGraph 控制异常、用户取消和 HITL interrupt。

### 4. 上下文管理分为稳定来源与 conversation

稳定来源每次 model call 从当前权威状态重建，不进入 conversation summary：

- 场景 system prompt；
- Skills metadata 与已加载 Skill 引用；
- Memory 文件；
- 当前时间、workspace、附件 manifest；
- 当前 plan/todo、delegation ledger、active file references；
- tool/MCP catalog 与已发现 deferred tools；
- Provider capability instructions。

Conversation 包含用户消息、assistant 消息、tool call/result、决策过程和任务进展，可以被局部减重或摘要。

Middleware 之间通过 LangGraph typed/private state 传递持久状态，不使用进程级 `ContextVar` 串联控制决策。private state 不进入子 Agent input，也不出现在 Agent final output。

### 5. RefreshingSkillsMiddleware 与 RefreshingMemoryMiddleware

DeepAgents `0.6.12` 的 Skills 和 Memory private state 默认只加载一次。两类来源的 freshness 语义不同，不建立统一 `SourceRefreshMiddleware`：

- `RefreshingSkillsMiddleware` 比较当前用户 Skills revision；仅在 revision 变化时清理 `skills_metadata` / `skills_load_errors`，随后调用上游 loader。
- `RefreshingMemoryMiddleware` 在每个顶层 invocation 开始时清理 `memory_contents` 并调用上游 loader；同一 run 内保持加载结果稳定。Memory 具备可靠 revision 后再改成 revision-based reload。
- tool catalog 使用 catalog hash，attachments 每轮由 resolver 解析，日期/workspace 由 DynamicContext 注入，各 owner 自己定义 freshness。

两个类都是 DeepAgents middleware 的薄适配，不维护第二套 parser 或 prompt。

### 6. DynamicContextMiddleware

职责：

- 在每次 model call 注入当前日期/时区、workspace/session 标识和附件索引；
- 读取场景已经解析好的 run context，不访问数据库或 Service；
- 生成稳定、可缓存的 block 顺序，动态 block 放在静态 prompt 之后；
- compaction 后自然重新执行，保证动态来源恢复。

它不扫描 Skills/Memory、不做 token 预算、不修改 history、不记录 usage。

### 7. DurableContextMiddleware

Claude Code 压缩后会重新注入 plan、Skills、工具/MCP、文件等附件。Noesis 用 private state保存摘要外的最小 durable context：

```text
active_plan_ref
pending_tasks[]
delegation_ledger[]
loaded_skill_refs[]
active_file_refs[]
discovered_tool_refs[]
user_compact_instructions
```

该 middleware 在 tool/subagent 完成后更新 ledger，在每次 model call 注入有界状态。它保存引用和任务事实，不复制完整 tool result、Skill 正文或文件正文。

Compaction 只能摘要 conversation，不能删除 durable context。子 Agent 默认不继承该 state；fork 模式按白名单复制。

### 8. ReadBeforeWriteMiddleware

为了接近 Claude Code 的 coding context 行为，filesystem Profile 增加确定性的版本门禁：

- Read 成功后在对应 ToolMessage metadata 中记录 path 与完整内容 hash；
- Edit/Write 前重新读取当前版本，已有文件必须存在与当前 hash 一致的 read mark；
- 任何成功写入都会改变 hash，使之前的 mark 自动失效，连续修改前必须重新读取；
- 同一 `(thread/sandbox, path)` 的 read/check/write 使用同一临界区，避免并发写共享旧 mark；
- 无法检查 backend 时让实际工具产生权威错误，不伪造成功。

active file references 由 DurableContext 保存。Noesis 不再维护职责过大的 FileContext LRU、excerpt 恢复和另一套文件缓存。

### 9. SnipMiddleware

Claude Code 允许在保留 transcript 的同时，从 Provider effective history 定点移除内容。Noesis 的 Snip：

- 接收明确的 message/block selector，不根据 prompt 猜测目标；
- 只改变 effective history projection，不物理删除 checkpoint/raw transcript；
- 记录 replacement marker、原因、原内容 hash 和 `tokens_freed`；
- resume 后重放相同 projection；
- 不允许删除 compact boundary、当前用户请求或 tool pair 的一半。

Snip 可由用户操作、系统 policy 或已知无价值内容触发；释放 token 必须进入后续预算计算。没有真实 selector 入口时不默认装配。

### 10. Tool Result Micro-compaction

每次完整 compaction 前先执行不调用模型的局部减重：

1. 保留最近窗口内的 tool call/result 原文；
2. 对旧的大 Tool Result 优先替换为 artifact path + synopsis；
3. 对旧 write/edit 等大参数保留开头、hash、目标路径和结果状态；
4. 删除重复的动态附件和已失效 tool/MCP delta；
5. 不切断 tool call/result、thinking block 或 API round。

这不是独立 middleware：Tool Result 和旧 write/edit 参数的有界化由 `ToolResultBudgetMiddleware.wrap_model_call` 完成；其余 conversation reduction 由 Compaction 完成。如果 Provider 支持 cache editing，provider adapter 可以表达等价 projection。原始 checkpoint messages 不被物理删除。

### 11. Runtime Tool Registry 与 DeferredToolFilterMiddleware

Claude Code 会根据 ToolSearch 动态加载 deferred MCP tools。Noesis 目标行为：

- 计算全部 tool schemas 的 token 占用；
- 基础工具与当前已激活工具始终绑定；
- 大型 MCP/extension 工具标记 deferred，不默认把完整 schema 发给模型；
- 提供 `tool_search`，搜索结果把对应 schema 加入当前 run 的 discovered set；
- MCP server 连接变化形成有界 delta；compaction 后从 catalog 重建；
- Provider 原生支持 deferred tool 时使用原生字段，否则由 middleware 动态过滤 `request.tools`。

`ToolRegistry` 是 MCP/tool 连接、schema、revision、搜索和执行授权的权威源，并向 factory 提供真实 `tool_search` 工具。`DeferredToolFilterMiddleware` 只操作 `request.tools` 和 tool-call gate：过滤未 promoted schema，并阻止模型绕过搜索直接调用。权限检查在执行时由 registry 再次完成，不能因为 schema 已发现就绕过授权。

### 12. CompactionMiddleware

`CompactionMiddleware` 是自包含的 `wrap_model_call` 适配层：它截获最终 ModelRequest、决定是否压缩，并在 Provider 真正返回 context overflow 时执行一次 reactive recovery。archive、summary、boundary 与 checkpoint 提交等压缩事务**全部在 middleware 内部完成**，不调用 `runtime/compaction.py` 或任何 service。依赖只有 factory 注入的 `model`（summary 调用）、`BackendProtocol`（archive 写入）和 `token_counter`。

压缩引擎使用 DeepAgents 的 backend archive、raw-state summarization event、message partition 和 overflow tail clip；触发、摘要模板和失败状态按 Claude Code 语义实现。不新增 `ContextCompiler` 或第二套 Agent loop。

#### 12.1 阈值

```text
effective_limit = model_input_limit - summary_output_reserve
auto_compact_at = effective_limit - transient_request_buffer
hard_stop_at = effective_limit - final_request_guard
```

具体值来自 model catalog/config 与 trace，不复制 Claude Code 固定常量。预算覆盖最终 system、messages、tool results、tool schemas、attachments 与 Provider framing estimate。

#### 12.2 压缩模式

- `incremental`：保留已有 boundary/summary，只摘要新增前缀；
- `full`：首次或手动完整摘要；
- `prefix`：summary request 自身超限时，按完整 API round 丢弃最旧前缀；
- `reactive`：Provider 返回 context overflow 后立即压缩并重试一次；
- `manual`：用户显式 compact，可附带本次保留指令。

摘要 prompt 使用结构化段落，至少覆盖用户目标、关键技术、文件/代码、错误与修复、已否决方案、全部用户要求、待办、当前工作和下一步。Summary model 禁用业务 tools，并设置 recursion guard。

#### 12.3 结果与恢复

压缩顺序：

```text
ToolResultBudget / Snip
→ archive evicted conversation
→ summary + preserved tail
→ compact boundary
→ rebuild Dynamic/Durable/Skills/Memory/Deferred tool state
→ canonical request
```

空 summary、错误标记文本和无有效 `<summary>` 内容都算失败，不发布新的 summarization event。raw history 与 archive 保持可恢复。

事务边界是：先生成 archive、summary、preserved tail、boundary 和待恢复的 stable-source refs；全部校验成功后再一次提交 checkpoint。任一步失败都不得先清空 history、file state 或 discovered tools。

#### 12.4 PTL retry 与 breaker

- summary request prompt-too-long 时按完整 API round 丢弃最旧前缀；
- 解析 Provider token gap；无法解析时按配置比例丢弃；
- 每次重试必须产生进展且不能切断 tool pair；
- 达到 PTL retry 上限后失败；
- 连续自动 compaction 失败达到上限后停止自动尝试；
- manual compact 不受自动 breaker 限制；
- 用户下一轮或成功手动 compact 可以按策略重置 breaker。

manual compact 是 host/runtime 命令，可由 UI/API 或可选 tool 触发；它与自动压缩调用同一引擎，不维护第二套 summary 实现。

### 13. Subagent context 边界

Noesis **不重写** `SubAgentMiddleware` 的编译、调度与结果回传。这部分直接复用 DeepAgents `SubAgentMiddleware` / `AsyncSubAgentMiddleware`。Factory 把 Noesis 的 dynamic、durable、file hash、tool-result、tool-catalog、snip 与 compaction state 全部传入上游公开的 `private_state_keys`，子 Agent 默认只得到任务描述和上游允许的非 private state。

DeepAgents `0.6.12` 没有公开的 state-builder callback；显式 conversation fork 和子 Agent checkpoint resume 不能只靠 `private_state_keys` 表达。当前版本不复制上游私有 `_build_task_tool`，也不重新建立 `SubAgentContextMiddleware`。这两种模式保留为后续上游公开扩展点或依赖升级任务，不在本次 middleware 实现中伪造完成。

### 14. Tool 与 Model Middleware

`ToolFailureMiddleware`：只将普通工具异常转换为同 call id 的 typed error ToolMessage。

`ToolResultBudgetMiddleware`：对聚合结果执行确定性 content replacement，保存 artifact path、synopsis、hash 和 replacement record；resume 后保持相同决策。Filesystem/artifact 处理后仍超限时执行最终文本兜底，不改变 status/category/outcome。

不保留 `SafeModelRetryMiddleware`。瞬时 HTTP 错误由 Provider SDK/adapter 在流式 body 开始前重试；context overflow 交回 Compaction。禁止在 inner handler 中偷偷执行 `empty_after_tools` 第二次调用。

普通 model/tool call limits 使用 LangChain；语义不满足时才替换为 Noesis 实现。

### 15. Provider Request Adapter

LangChain 的具体 Provider adapter 在真正发送前生成唯一 canonical request，负责：

- system block 合并与顺序稳定；
- role/message 规范化，tool call id 与 result 配对；
- thinking/media block 的 Provider 约束；
- tool schema 稳定排序、deferred schema 字段和 cache marker；
- 可缓存静态 prefix 与本轮动态 delta 的分界。

Noesis 不增加第二个 Provider adapter。`PatchToolCallsMiddleware` 只负责中断/恢复留下的不完整 tool pair；Compaction 在所有 Noesis model wrapper 完成 system/messages/tools projection 后计算预算，最终 Provider 专有 framing 与 cache marker 继续由上游 adapter 负责。

### 16. Profile 能力

| Profile | 必需 context 能力 | 可选能力 |
|---|---|---|
| `COMMON_QA` | DynamicContext、ToolResultBudget、Compaction、PatchToolCalls、ToolFailure | Snip、DeferredToolFilter、call limits、manual compact |
| `SUPER_AGENT_QA` | 上述全部 + ReadBeforeWrite、DurableContext、Filesystem、RefreshingSkills、RefreshingMemory、Todo、SubAgent、DeferredToolFilter | Snip、manual compact、HITL、call limits |
| `FAULT_OPERATION_QA` | DynamicContext、DurableContext、ToolResultBudget、Compaction、PatchToolCalls、ToolFailure、SubAgent、DeferredToolFilter | ReadBeforeWrite、Snip、HITL、call limits |
| `SimpleMCP` | DynamicContext、ToolResultBudget、Compaction、DeferredToolFilter、PatchToolCalls、ToolFailure | Snip、call limits、manual compact |
| 子 Agent | DeepAgents 默认 isolated task context、ToolResultBudget、Compaction、PatchToolCalls、ToolFailure | ReadBeforeWrite、RefreshingSkills、Filesystem、DeferredToolFilter、Snip；fork/resume 待上游扩展点 |

`TEST_CASE_QA` 继续使用 CaseCoordinator StateGraph，不进入该 factory。

### 17. Claude Code 能力覆盖

| Claude Code 能力 | Noesis 目标 |
|---|---|
| stable prompt/context source assembly | RefreshingSkills/Memory + DynamicContext + DurableContext |
| read file state、stale、写前校验 | ReadBeforeWrite |
| deterministic tool-result replacement | ToolResultBudget + Filesystem |
| explicit snip projection | Snip |
| tool result/argument microcompact | ToolResultBudget + Compaction |
| ToolSearch/deferred MCP schema | ToolRegistry + DeferredToolFilter |
| final request token budget | Compaction `_context_budget` |
| auto/incremental/manual compact | Compaction |
| structured summary、summary no-tools | Compaction summary policy |
| archive、raw history、preserved tail | DeepAgents engine |
| summary PTL retry、reactive overflow | Compaction |
| consecutive failure breaker | Compaction |
| post-compact stable source rebuild | 各 stable-source middleware重新执行 |
| subagent 默认 isolated | 上游 SubAgentMiddleware + `private_state_keys`；fork/resume 待上游公开 state-builder hook |
| provider prompt cache | Provider PromptCaching middleware |
| provider message/schema canonicalization | Provider request adapter + PatchToolCalls |
| usage/context visualization | stream + internal context events |

唯一不承诺完全一致的是 Anthropic 私有 cache-editing 协议；Noesis 保持语义相同的本地 effective-message 路径。

### 18. DeerFlow 迁移

DeerFlow 的大量 middleware 仍作为迁移检查表，但不决定目录或类数量：

| DeerFlow 能力 | Noesis 目标 |
|---|---|
| ThreadData、Uploads、Sandbox | runtime attachments + backends |
| DanglingToolCall | PatchToolCalls |
| ToolError/ToolOutputBudget | ToolFailure + Filesystem + ToolResultBudget |
| Dynamic/Durable/Summarization | DynamicContext + DurableContext + Compaction |
| SkillActivation/Memory/Todo | DeepAgents/LangChain + DurableContext refs |
| MCPRouting/DeferredToolFilter | ToolRegistry + DeferredToolFilter |
| ReadBeforeWrite | ReadBeforeWrite |
| SubagentLimit | LangGraph 节点执行机制 + subagent state builder |
| LoopDetection/TokenBudget | call limits + Compaction pressure；独立 run budget 按配置实现 |
| SystemMessageCoalescing | Provider adapter 契约 |
| TokenUsage/Title/ToolProgress | stream/service |
| Terminal/Length/Safety | delivery + tool dispatch safety |
| Guardrail/Input/Result sanitization | 单独安全策略，不与 context state 混写 |

### 19. 删除旧五 Owner

- `RuntimeTelemetryMiddleware`：Provider usage 和 context event 进入 stream/trace。
- `ToolExecutionMiddleware`：迁到 ToolFailure、ToolResultBudget、ReadBeforeWrite、Filesystem 和 SubAgent。
- `RunGovernorMiddleware`：迁到 LangChain call limits、runtime task registry 与明确 run budget。
- `ContextLifecycleMiddleware`：迁到 DynamicContext、ToolResultBudget、Compaction、PatchToolCalls、DurableContext。
- `ModelExecutionMiddleware`：迁到 Provider SDK/adapter 与 delivery。

删除前必须建立字段级迁移表，覆盖配置键、state schema、stop reason、ToolMessage metadata、SSE 事件和测试。没有迁移去向或明确删除理由的行为不得直接移除。

### 20. 观测与 API 兼容

LangChain raw event → stream bridge → SSE → assistant persistence 不重写。

新增内部 context event：

```text
context_budget
skills_revision_changed / memory_refreshed
tool_result_replaced
snip_applied
tool_result_projection
tool_catalog_changed
file_context_changed
compaction_started/completed/failed
compaction_ptl_retry
compaction_breaker_open
subagent_context_created
```

`context-update.current_tokens` 继续使用 Provider 最近一次真实 `input_tokens`；本地 estimate 只用于决策和诊断。`usage-update` 继续表示 run 累计 usage。新增字段默认不进入用户文案。

## Risks / Trade-offs

- [自定义 middleware 增加] → 每个类只拥有一个 Claude Code 阶段，并通过 hook/state 契约测试固定边界。
- [DeepAgents 升级改变内部 engine] → pin 单一版本；只调用公开 API，缺少扩展点时优先提交上游或使用 composition，不 fork 整个包。
- [Dynamic/Durable context 重复注入] → source id 唯一；最终 request fixture 断言每个 block 只出现一次。
- [Tool Result projection 与 DeepAgents truncation 重复] → 关闭上游同义 truncation，保留单一 owner。
- [DeferredToolFilter 隐藏必要工具] → 基础工具永不 deferred；tool_search 结果和执行权限独立校验；Provider 不支持时有本地 fallback。
- [ReadBeforeWrite 增加 I/O] → 只在实际 read/write/edit 工具触发，不扫描整个 workspace。
- [Compaction adapter 逐渐复制 DeepAgents] → archive、partition、raw event 和 tail clip 继续由上游 engine负责；Noesis 只持有 Claude policy 与恢复状态。
- [子 Agent fork 泄漏 private state] → whitelist + deep copy + private-state fixture，默认 isolated。

## Migration Plan

1. 固定当前五 owner 的字段级行为、真实 Profile stack、SSE/stop reason 与 assistant persistence fixture。
2. pin DeepAgents 版本，验证 Summarization engine、Filesystem、Skills、Memory、PatchToolCalls、SubAgent private state 和 prompt caching。
3. 建立 `factory.py` 与 `agents/middlewares/`、`agents/backends/`、`agents/runtime/`，并固定 lazy 场景导出和公共 middleware 的反向依赖禁令。
4. 实现 DynamicContext、DurableContext、RefreshingSkills/Memory 与 ReadBeforeWrite，验证 stable sources 和 stale file。
5. 实现 ToolResultBudget、Snip、ToolRegistry 与 DeferredToolFilter，验证 tool pair、artifact synopsis、deferred schema 和 tool search。
6. 实现 Compaction，覆盖 incremental/full/prefix/reactive/manual、reserve/buffer、structured summary、PTL retry 和 breaker。
7. 通过上游 `private_state_keys` 实现默认 isolated；不复制上游私有 task-tool builder，fork/resume 留给后续公开扩展点。
8. 迁移 ToolFailure、call limits 与 Provider adapter，删除 middleware model retry。
9. 按字段级迁移表删除旧五 owner、ContextVar 链、手写 inventory、旧目录和失效配置。
10. 运行 backend 全量测试、各 Profile E2E、长上下文压力测试、Provider overflow、MCP 大 catalog、文件 stale、子 Agent fork 与 `/api/chat` 回归。
11. 实施完成后将工程文档更新为 Current。

每个阶段独立提交并可单独回滚，不保留长期 feature flag 双轨。

## Open Questions

无。实现时若上游新增行为完全一致的公开 middleware，删除对应 Noesis 实现并更新本设计。
