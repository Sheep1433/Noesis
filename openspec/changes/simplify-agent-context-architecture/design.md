## Context

Noesis 当前通过 `factory.py` 调用 LangChain `create_agent()`，并把运行行为集中到五个宏观 middleware：

```text
RuntimeTelemetry → ToolExecution → RunGovernor → ContextLifecycle → ModelExecution
```

这套结构的问题不是 middleware 数量，而是职责与生命周期不一致：prompt、message repair、compaction、工具结果、retry、预算和观测通过多个 hook 与 `ContextVar` 相互影响。Super task-worker 还会在 factory 返回完整 stack 后追加 Skills，手写 inventory 与真实顺序已经分叉。

本设计重新确定两个独立目标：

1. **上下文行为以 Claude Code 2.1.88 为基线。** Noesis 应具有近似的稳定来源装配、局部减重、自动/手动压缩、失败恢复、工具 schema 控制、文件状态、子 Agent 隔离和可观测性。Noesis 当前没有的能力也应纳入目标。
2. **代码组织参考 DeepAgents。** 保持一个 factory/graph 装配入口、平铺 `middleware/`、`backends/` 与直接构造参数；不引入 context compiler、可执行 spec 或多层 kernel 目录。

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

### 1. 目录采用 DeepAgents 风格

```text
noesis/
├── factory.py                     # 唯一 ReAct Agent 装配入口
├── middleware/
│   ├── dynamic_context_middleware.py    # 每次请求重建动态稳定来源
│   ├── source_refresh_middleware.py     # Skills/Memory/tool catalog revision 刷新
│   ├── durable_context_middleware.py    # plan/task/skill/file 引用等压缩外状态
│   ├── file_context_middleware.py       # read state、stale、写前校验、关键文件恢复
│   ├── snip_middleware.py               # 定点移除 effective history 内容
│   ├── micro_compaction_middleware.py   # 不调用模型的局部减重
│   ├── compaction_middleware.py         # 自动/手动压缩与失败恢复
│   ├── tool_catalog_middleware.py       # deferred tool schema 与 tool search
│   ├── subagents_middleware.py       # 子 Agent context policy（isolated/fork/resume）；复用上游 SubAgentMiddleware 的编译/调度/结果回传
│   ├── tool_failure_middleware.py
│   ├── tool_result_budget_middleware.py # replacement record、artifact、最终兜底
│   ├── safe_model_retry_middleware.py
│   ├── _context_budget.py               # 私有纯函数，不是 middleware
│   └── _summary_prompt.py              # 私有摘要模板
├── backends/                      # workspace、archive、artifact adapter（注入给 middleware，不被 middleware 运行时调用）
├── agents/                        # 场景 prompt、场景 tools、Agent 入口
└── runtime/
    ├── tool_registry.py           # MCP/tool 连接、schema 与权限权威源（factory 装配时消费）
    ├── providers/                 # request canonicalization 与 capability adapter
    └── ...                        # streaming、attachments、HITL host、日志
```

DeepAgents/LangChain 的公开 middleware 直接从依赖导入，不复制到 Noesis。`middleware/` 只保存行为不同或上游缺失的 Noesis 实现。每个 Noesis middleware 自包含：只依赖 factory 注入的依赖（model、`BackendProtocol`、token_counter、compiled subagents 等）和 LangGraph typed/private state，不在运行时调用 `runtime/`、`service` 或其它 Noesis 模块。archive、summary、boundary、checkpoint 提交等压缩事务全部在 `CompactionMiddleware` 内部完成，无外置 runtime 事务服务。

`factory.py` 可以导入 `middleware`、`backends` 与 `runtime`；这些公共包不得导入具体 `agents` 场景。删除 `agents.__getattr__` lazy import。

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
| `skills` | `SkillsMiddleware(sources=skills)`（DeepAgents） |
| `memory` | `MemoryMiddleware(sources=memory)`（DeepAgents） |
| `subagents` | `SubAgentMiddleware` / `AsyncSubAgentMiddleware`（DeepAgents） |
| `backend` | `FilesystemMiddleware(backend=backend)`（DeepAgents） |
| `interrupt_on` | `HumanInTheLoopMiddleware`（LangChain） |

参数签名采用 DeepAgents 风格仅为统一调用方体验；DeepAgents 的公开中间件直接从依赖导入，不复制到 Noesis `middleware/` 目录。`middleware/` 只保存行为不同或上游缺失的 Noesis 实现。Factory 一次性完成映射与 stack 构造；调用方不得在 factory 返回后继续 append capability，诊断 inventory 从实际实例列表生成，不维护独立 allowlist。

现有 `COMMON_QA / SUPER_AGENT_QA / FAULT_OPERATION_QA / SimpleMCP` 名称继续用于配置和观测，不新增另一套 Profile class。场景 Agent 负责准备参数。主 Agent、子 Agent 与离线评测都使用该入口。

### 3. 目标 Middleware Stack

Outer-to-inner 模板如下，可选项省略时不改变其余相对顺序：

```text
ToolResultBudgetMiddleware                # Noesis
→ ToolFailureMiddleware                   # Noesis
→ FileContextMiddleware                   # Noesis
→ SourceRefreshMiddleware                 # Noesis
→ TodoListMiddleware                      # LangChain，可选
→ SkillsMiddleware                        # DeepAgents，可选
→ FilesystemMiddleware                    # DeepAgents，可选
→ SubAgentContextMiddleware              # Noesis context policy（isolated/fork/resume），可选；复用上游 SubAgentMiddleware 编译/调度/结果回传
→ MemoryMiddleware                        # DeepAgents，可选
→ DynamicContextMiddleware                # Noesis
→ DurableContextMiddleware                # Noesis
→ SnipMiddleware                          # Noesis
→ MicroCompactionMiddleware               # Noesis
→ ToolCatalogMiddleware                   # Noesis，工具目录达到阈值时启用
→ PatchToolCallsMiddleware                # DeepAgents
→ CompactionMiddleware                    # Noesis，组合 DeepAgents engine
→ configured ModelCallLimitMiddleware     # LangChain，可选
→ configured ToolCallLimitMiddleware      # LangChain，可选
→ SafeModelRetryMiddleware                # Noesis
→ Provider PromptCachingMiddleware        # 上游/provider adapter，可选
→ HumanInTheLoopMiddleware                # LangChain，可选
→ Provider
```

关键顺序：

- ToolResultBudget 位于 ToolFailure 外层，最终 error ToolMessage 也必须有界。
- FileContext 位于 Filesystem 外层，能够观察 read/write/edit 结果以及 backend 异常。
- SourceRefresh 先于 Skills/Memory 执行；Skills、Memory、DynamicContext、DurableContext、ToolCatalog 和 PatchToolCalls 都位于 Compaction 外层，使 Compaction 看到最终 system/messages/tools。
- MicroCompaction 先执行不调用模型的减重，Compaction 再判断是否需要摘要。
- SafeModelRetry 位于 Compaction 内层，瞬时 retry 使用同一份已处理 request；`ContextOverflowError` 必须返回 Compaction，不由普通 retry 捕获。
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

### 5. SourceRefreshMiddleware

DeepAgents `0.6.12` 的 Skills 和 Memory private state 默认只加载一次。Noesis 必须实现 Claude Code 式 source revision：

- 每个顶层用户 turn 计算 Skills、Memory、tool catalog、attachments 和场景 prompt fingerprint；
- revision 未变化时保持 state 与 prompt prefix 稳定；
- revision 变化时只清理对应上游 private cache，使 Skills/Memory 在本轮开始重新加载；
- 同一 run 内固定 revision，避免工具写 Memory 后下一次 model call 突然改变 prompt；
- compact、subagent fork 和 resume 都携带明确 revision，不依赖进程局部缓存。

该 middleware 只负责 freshness/invalidation，不解析 Skill、Memory 或 MCP schema 正文。

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

### 8. FileContextMiddleware

为了接近 Claude Code 的 coding context 行为，Super/Fault filesystem Profile 增加文件状态：

- 使用有界 LRU 保存 path、mtime/hash、读取范围和最近访问时间；
- Read 成功后登记 file state；Edit/Write 前由 file tool/backend 根据同一 state 验证文件已经读取且没有变更；
- Bash/外部工具修改已读文件时标记 stale，并在下一次 model call 注入短提示；
- compaction 前记录 active file references；compaction 后按预算恢复最近关键文件的有界 excerpt；
- 子 Agent isolated 模式使用独立 cache；fork 模式克隆允许的 file state，不能共享可变容器。

文件读取、mtime/hash 校验和写入拒绝由 backend/tool adapter 执行；middleware 维护 model-facing file context、stale hint 与 compaction 恢复引用。

### 9. SnipMiddleware

Claude Code 允许在保留 transcript 的同时，从 Provider effective history 定点移除内容。Noesis 的 Snip：

- 接收明确的 message/block selector，不根据 prompt 猜测目标；
- 只改变 effective history projection，不物理删除 checkpoint/raw transcript；
- 记录 replacement marker、原因、原内容 hash 和 `tokens_freed`；
- resume 后重放相同 projection；
- 不允许删除 compact boundary、当前用户请求或 tool pair 的一半。

Snip 可由用户操作、系统 policy 或已知无价值内容触发；它与 MicroCompaction 同时生效，释放 token 必须进入后续预算计算。

### 10. MicroCompactionMiddleware

每次完整 compaction 前先执行不调用模型的局部减重：

1. 保留最近窗口内的 tool call/result 原文；
2. 对旧的大 Tool Result 优先替换为 artifact path + synopsis；
3. 对旧 write/edit 等大参数保留开头、hash、目标路径和结果状态；
4. 删除重复的动态附件和已失效 tool/MCP delta；
5. 不切断 tool call/result、thinking block 或 API round。

如果 Provider 支持 cache editing，provider adapter 可以把减重表达为 cache edit；否则生成本地 effective messages。原始 checkpoint messages 不被物理删除。

DeepAgents Summarization 的旧 tool-arg truncation 与此能力只能启用一个 owner。选用 Noesis MicroCompaction 后关闭上游同义截断，仍采用其 archive 和 summary engine。

### 11. ToolCatalogMiddleware

Claude Code 会根据 ToolSearch 动态加载 deferred MCP tools。Noesis 目标行为：

- 计算全部 tool schemas 的 token 占用；
- 基础工具与当前已激活工具始终绑定；
- 大型 MCP/extension 工具标记 deferred，不默认把完整 schema 发给模型；
- 提供 `tool_search`，搜索结果把对应 schema 加入当前 run 的 discovered set；
- MCP server 连接变化形成有界 delta；compaction 后从 catalog 重建；
- Provider 原生支持 deferred tool 时使用原生字段，否则由 middleware 动态过滤 `request.tools`。

ToolCatalog 只操作 `request.tools` 与自身 `tool_search` 工具；MCP/tool registry 负责连接和实际调用（factory 装配时绑定，middleware 不在运行时调用 registry）。权限检查在执行时重新验证，不能因为 schema 已发现就绕过授权。

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
ToolResultBudget / Snip / MicroCompaction
→ archive evicted conversation
→ summary + preserved tail
→ compact boundary
→ rebuild Dynamic/Durable/File/Skills/Memory/ToolCatalog
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

### 13. SubAgentContextMiddleware（context policy 层）

Noesis **不重写** `SubAgentMiddleware` 的编译、调度与结果回传——这部分直接复用 DeepAgents `SubAgentMiddleware` / `AsyncSubAgentMiddleware`（factory 装配时注入 compiled subagents + checkpointer）。上游 `_validate_and_prepare_state` 默认让子 Agent **继承父 Agent 全部 state**（仅硬编码排除 6 个 key：`messages`、`todos`、`structured_response`、`skills_metadata`、`skills_load_errors`、`memory_contents`，见 `subagents.py:240-247,534-539`），对 Noesis 自己引入的 private state（`active_file_refs`、`discovered_tool_refs`、`delegation_ledger`、`_summarization_event` 等）完全无知，会直接泄漏给子 Agent。

因此 Noesis 只自实现子 Agent **context policy**，作为一个自包含中间件，只操作 LangGraph state，不接管 subagent 的编译/调度/结果回传：

- `isolated` 默认：子 Agent 只接收任务描述 + 场景允许的白名单 stable context；父 Agent 的 conversation、file state、tool discovery、compaction state、durable ledger 全部隔离，不依赖上游的硬编码排除表扩展。
- `fork` 显式：复制父 conversation snapshot 与白名单 durable context；所有可变 state 深拷贝，后续互不影响。
- `resume`：从子 Agent 自身 checkpoint 恢复，不重新读取父 Agent 当前 state。

上游 result→ToolMessage 回传机制（`_return_command_with_state_update`）原样复用；private middleware state 不参与父子 merge。

context policy 通过 factory 注入的 `context_mode` 参数（per-subagent）选择，不调用 runtime task registry、service 或 scheduler。并发、取消、超时由 LangGraph 节点执行机制承载，不是 Noesis middleware 的运行时调用对象。

### 14. Tool 与 Model Middleware

`ToolFailureMiddleware`：只将普通工具异常转换为同 call id 的 typed error ToolMessage。

`ToolResultBudgetMiddleware`：对聚合结果执行确定性 content replacement，保存 artifact path、synopsis、hash 和 replacement record；resume 后保持相同决策。Filesystem/artifact 处理后仍超限时执行最终文本兜底，不改变 status/category/outcome。

`SafeModelRetryMiddleware`：重试瞬时错误，沿用同一份已 canonicalization/compaction/预算校验的 request。每个 attempt 单独可观测；context overflow 交回 Compaction。重试由 Provider SDK 的 HTTP 层负责（在流式 body 开始前根据状态码决定），middleware 层不重复实现可见输出检测——这与 Claude Code 2.1.88 的实际行为一致（SDK `maxRetries` 在 HTTP header 层重试，query loop 不检测已流式输出）。禁止在 inner handler 中偷偷执行 `empty_after_tools` 第二次调用。

普通 model/tool call limits 使用 LangChain；语义不满足时才替换为 Noesis 实现。

### 15. Provider Request Adapter

Provider adapter 在真正发送前生成唯一 canonical request，负责：

- system block 合并与顺序稳定；
- role/message 规范化，tool call id 与 result 配对；
- thinking/media block 的 Provider 约束；
- tool schema 稳定排序、deferred schema 字段和 cache marker；
- 可缓存静态 prefix 与本轮动态 delta 的分界。

`PatchToolCallsMiddleware` 只负责中断/恢复留下的不完整 tool pair，不代替 Provider canonicalization。预算使用 adapter 生成的最终 request，不使用早期 history 快照。

### 16. Profile 能力

| Profile | 必需 context 能力 | 可选能力 |
|---|---|---|
| `COMMON_QA` | SourceRefresh、DynamicContext、ToolResultBudget、Snip、MicroCompaction、Compaction、PatchToolCalls、ToolFailure、SafeModelRetry | ToolCatalog、call limits、manual compact |
| `SUPER_AGENT_QA` | 上述全部 + FileContext、DurableContext、Filesystem、Skills、Memory、Todo、SubAgent、ToolCatalog | manual compact、HITL、call limits |
| `FAULT_OPERATION_QA` | SourceRefresh、DynamicContext、DurableContext、ToolResultBudget、Snip、MicroCompaction、Compaction、PatchToolCalls、ToolFailure、SafeModelRetry、SubAgent、ToolCatalog | FileContext、HITL、call limits |
| `SimpleMCP` | SourceRefresh、DynamicContext、ToolResultBudget、Snip、MicroCompaction、Compaction、ToolCatalog、PatchToolCalls、ToolFailure、SafeModelRetry | call limits、manual compact |
| 子 Agent | isolated/fork policy、SourceRefresh、ToolResultBudget、Snip、MicroCompaction、Compaction、PatchToolCalls、ToolFailure、SafeModelRetry | FileContext、Skills、Filesystem、ToolCatalog |

`TEST_CASE_QA` 继续使用 CaseCoordinator StateGraph，不进入该 factory。

### 17. Claude Code 能力覆盖

| Claude Code 能力 | Noesis 目标 |
|---|---|
| stable prompt/context source assembly | SourceRefresh + DynamicContext + Skills/Memory + DurableContext |
| read file state、stale、写前校验 | FileContext |
| deterministic tool-result replacement | ToolResultBudget + Filesystem |
| explicit snip projection | Snip |
| tool result/argument microcompact | MicroCompaction |
| ToolSearch/deferred MCP schema | ToolCatalog |
| final request token budget | Compaction `_context_budget` |
| auto/incremental/manual compact | Compaction |
| structured summary、summary no-tools | Compaction summary policy |
| archive、raw history、preserved tail | DeepAgents engine |
| summary PTL retry、reactive overflow | Compaction |
| consecutive failure breaker | Compaction |
| post-compact stable source rebuild | 各 stable-source middleware重新执行 |
| subagent isolated/fork/resume | SubAgentContextMiddleware（context policy）+ 上游 SubAgentMiddleware（编译/调度） |
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
| MCPRouting/DeferredToolFilter | ToolCatalog |
| ReadBeforeWrite | FileContext |
| SubagentLimit | LangGraph 节点执行机制 + SubAgentContextMiddleware context policy |
| LoopDetection/TokenBudget | call limits + Compaction pressure；独立 run budget 按配置实现 |
| SystemMessageCoalescing | Provider adapter 契约 |
| TokenUsage/Title/ToolProgress | stream/service |
| Terminal/Length/Safety | delivery + tool dispatch safety |
| Guardrail/Input/Result sanitization | 单独安全策略，不与 context state 混写 |

### 19. 删除旧五 Owner

- `RuntimeTelemetryMiddleware`：Provider usage 和 context event 进入 stream/trace。
- `ToolExecutionMiddleware`：迁到 ToolFailure、ToolResultBudget、FileContext、Filesystem 和 SubAgent。
- `RunGovernorMiddleware`：迁到 LangChain call limits、runtime task registry 与明确 run budget。
- `ContextLifecycleMiddleware`：迁到 DynamicContext、MicroCompaction、Compaction、PatchToolCalls、DurableContext。
- `ModelExecutionMiddleware`：迁到 SafeModelRetry 与 delivery。

删除前必须建立字段级迁移表，覆盖配置键、state schema、stop reason、ToolMessage metadata、SSE 事件和测试。没有迁移去向或明确删除理由的行为不得直接移除。

### 20. 观测与 API 兼容

LangChain raw event → stream bridge → SSE → assistant persistence 不重写。

新增内部 context event：

```text
context_budget
source_revision_changed
tool_result_replaced
snip_applied
micro_compaction
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
- [Dynamic/Durable/File context 重复注入] → source id 唯一；最终 request fixture 断言每个 block 只出现一次。
- [MicroCompaction 与 DeepAgents truncation 重复] → 关闭上游同义 truncation，保留单一 owner。
- [ToolCatalog 隐藏必要工具] → 基础工具永不 deferred；tool_search 结果和执行权限独立校验；Provider 不支持时有本地 fallback。
- [FileContext 增加 I/O] → LRU 与恢复预算有上限；mtime/hash 检查按实际工具触发，不扫描整个 workspace。
- [Compaction adapter 逐渐复制 DeepAgents] → archive、partition、raw event 和 tail clip 继续由上游 engine负责；Noesis 只持有 Claude policy 与恢复状态。
- [子 Agent fork 泄漏 private state] → whitelist + deep copy + private-state fixture，默认 isolated。

## Migration Plan

1. 固定当前五 owner 的字段级行为、真实 Profile stack、SSE/stop reason 与 assistant persistence fixture。
2. pin DeepAgents 版本，验证 Summarization engine、Filesystem、Skills、Memory、PatchToolCalls、SubAgent private state 和 prompt caching。
3. 建立 DeepAgents 风格 `factory.py` 与顶层 `middleware/`、`backends/`，暂不删除旧实现。
4. 实现 DynamicContext、DurableContext 与 FileContext，验证 stable sources、stale file 和 post-compact rebuild。
5. 实现 MicroCompaction 与 ToolCatalog，验证 tool pair、artifact synopsis、deferred schema 和 tool search。
6. 实现 Compaction，覆盖 incremental/full/prefix/reactive/manual、reserve/buffer、structured summary、PTL retry 和 breaker。
7. 实现 SubAgentContextMiddleware 的 isolated/fork/resume context policy，复用上游 SubAgentMiddleware 的编译/调度/结果回传，context_mode 通过 factory 注入。
8. 迁移 ToolFailure、ToolResultBudget、SafeModelRetry、call limits 与 Provider adapter。
9. 按字段级迁移表删除旧五 owner、ContextVar 链、手写 inventory、旧目录和失效配置。
10. 运行 backend 全量测试、各 Profile E2E、长上下文压力测试、Provider overflow、MCP 大 catalog、文件 stale、子 Agent fork 与 `/api/chat` 回归。
11. 实施完成后将工程文档更新为 Current。

每个阶段独立提交并可单独回滚，不保留长期 feature flag 双轨。

## Open Questions

无。实现时若上游新增行为完全一致的公开 middleware，删除对应 Noesis 实现并更新本设计。
