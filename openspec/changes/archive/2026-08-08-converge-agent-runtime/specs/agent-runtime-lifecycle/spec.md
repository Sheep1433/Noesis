## ADDED Requirements

### Requirement: 公共 Runtime SHALL 按五类职责组织

系统 SHALL 将 ReAct Agent 的公共运行时职责限定为 Context Lifecycle、Model Execution、Tool Execution、Run Governor 与 Runtime Telemetry，并 SHALL 以 LangChain `AgentMiddleware` lifecycle hook 作为 `create_agent` 的权威接入点。Middleware MAY 将纯计算、存储或 policy 委托给 runtime service，但同一状态与决策 SHALL 只有一个权威 owner；系统 SHALL NOT 绕开 LangChain Agent loop 再建第二套执行循环，也 SHALL NOT 为 length stop、empty terminal、tool output budget 等单点问题继续叠加相互独立且依赖顺序的补丁中间件。

#### Scenario: 工厂装配公共 Runtime

- **WHEN** `create_noesis_agent` 装配任一 ReAct Agent Profile
- **THEN** Agent SHALL 获得同一套公共 runtime middleware lifecycle
- **AND** Profile capability SHALL NOT 复制其中任一 owner 的状态机

#### Scenario: Middleware 委托内部 Service

- **WHEN** Context Lifecycle 或 Tool Execution 需要 artifact 存储、token 估算或 policy 计算
- **THEN** 对应 middleware MAY 调用无 Agent loop 控制权的内部 service
- **AND** continue/retry/stop 与 state update SHALL 仍由该 middleware hook 返回给 LangChain

### Requirement: Context Lifecycle SHALL 规范化并压缩模型上下文

Context Lifecycle SHALL 在模型请求前使用同一份最终 context snapshot 完成 tool call/output 配对修复、上下文预算判断与 compaction。Compaction SHALL 区分持久 history 与可从权威来源重新构造的 context source；压缩完成后 Skills、Memory、任务信息等长期上下文 SHALL 从来源重建，瞬时时间提示与调试信息 SHALL NOT 被固化进 summary。

#### Scenario: dangling tool call 后继续

- **WHEN** 恢复的 history 含没有对应 ToolMessage 的 tool call
- **THEN** Context Lifecycle SHALL 在请求 Provider 前补齐或剥离该不完整配对
- **AND** Provider SHALL NOT 因协议配对错误拒绝请求

#### Scenario: 压缩后重建长期上下文

- **WHEN** Agent 触发 compaction 后继续模型调用
- **THEN** 当前启用的 Skills、Memory 与任务上下文 SHALL 从权威来源重新加入最终请求
- **AND** 旧的动态提示 SHALL NOT 因 summary 被重复注入

#### Scenario: 压缩后仍超过窗口

- **WHEN** tool output 有界化和 compaction 后最终 ModelRequest 仍超过模型输入上限
- **THEN** Context Lifecycle SHALL 返回结构化 `context_exhausted` outcome
- **AND** SHALL NOT 将超限请求发送给 Provider

### Requirement: Model Execution SHALL 产生统一 Outcome

每次模型调用 SHALL 产生统一 model execution outcome，至少区分 `completed`、`retryable_error`、`length_stop`、`safety_stop`、`context_exhausted`、`partial_output` 与 `empty_after_tools`。模型重试 SHALL 仅发生在确认可重试且尚未产生用户可见输出、工具调用或 HITL 副作用时；达到边界后 SHALL 保留已有输出并终止，SHALL NOT 重放整个 step。

#### Scenario: 流开始前连接中断

- **WHEN** Provider 在产生可见 token 或 tool call 前返回可重试连接错误
- **THEN** Model Execution MAY 按配置 backoff 重试
- **AND** SHALL 发出可观测的 retry attempt 状态

#### Scenario: 已有文本后连接中断

- **WHEN** Provider 已产生用户可见文本后连接中断
- **THEN** Model Execution SHALL 返回 `partial_output`
- **AND** SHALL NOT 重试并产生重复文本

#### Scenario: 工具后模型空终态

- **WHEN** 当前 model step 的 request 末尾包含至少一个已结束工具结果，而后续模型调用没有正文、tool call 或 HITL 请求
- **THEN** Model Execution SHALL 通过只作用于本次 request 的瞬时收敛提示最多再次调用 model handler 一次
- **AND** SHALL NOT 将该提示写入 conversation state 或重放工具
- **AND** 若再次为空，SHALL 返回固定可见 fallback 并记录 `empty_after_tools`
- **AND** runtime SHALL NOT 静默完成或无限重试

### Requirement: Tool Execution SHALL 使用统一结果 Envelope

每次 in-scope 工具调用写入 Agent history 前 SHALL 归一为包含 `status`、`content` 和可选 `category`、`outcome` 的内部结果 envelope。超出配置预算的正文 SHALL 在工具返回边界被有界化。已挂载 DeepAgents `FilesystemMiddleware` 时 SHALL 优先采用其通用 tool-result offload；`ToolExecutionMiddleware` SHALL 将 offload 后的 ToolMessage 作为权威 content，SHALL NOT 解析第三方提示文本来伪造结构化 artifact metadata。未挂载 filesystem capability、工具被第三方 offload 排除或返回结果仍未有界时，Noesis SHALL 执行一次 fallback head/tail 截断；SHALL NOT 对已处理结果二次转存或等待整体 context 接近上限后才处理。

#### Scenario: 大工具结果写入 artifact

- **WHEN** 工具正文超过单结果预算且 Agent 具有当前 session filesystem backend
- **THEN** DeepAgents `FilesystemMiddleware` SHALL 优先将完整正文写入该 session 的 artifact 路径
- **AND** ToolMessage SHALL 使用 DeepAgents 生成的文件引用、省略说明与有界 preview

#### Scenario: 无 filesystem 的大结果

- **WHEN** 工具正文超过预算且当前 Agent 没有可写 artifact backend
- **THEN** ToolMessage SHALL 保留配置允许的头尾和明确省略标记
- **AND** SHALL NOT 将未限制的完整正文写入 history

#### Scenario: 已有第三方 offload 标记

- **WHEN** Tool Execution 收到已经包含 DeepAgents large-tool-result 路径和 preview 的 ToolMessage
- **THEN** SHALL 原样保留该有界 content
- **AND** SHALL NOT 从提示文本解析路径、再次截断 preview 或创建 Noesis artifact

### Requirement: Run Governor SHALL 统一运行预算

Run Governor SHALL 以 `run_id` 为作用域维护模型调用、工具调用、重复调用窗口、子 Agent 活跃数/总数/深度及可选累计 token 预算。所有限制 SHALL 产生稳定 stop reason；主 Agent 与子 Agent SHALL 使用同一预算模型，子 Agent 的本地限制 MAY 更严格但 SHALL NOT 绕过父 run 的总预算。

#### Scenario: 子 Agent 并发槽位耗尽

- **WHEN** 活跃子 Agent 数已达到配置上限且模型再次委派
- **THEN** Run Governor SHALL 拒绝新委派并返回稳定的 `subagent_concurrency_limit` reason
- **AND** 已运行子 Agent SHALL 不受影响

#### Scenario: 重复工具循环

- **WHEN** 同一 run 的工具调用在配置窗口内达到循环硬限制
- **THEN** Run Governor SHALL 停止继续调用工具并返回 `tool_loop_limit`
- **AND** 最终响应 SHALL 保留停止前已有结果

#### Scenario: token attribution 尚不可用

- **WHEN** runtime 无法获得去重后的实际 Provider usage
- **THEN** Run Governor SHALL 不启用实际累计 token 硬限制
- **AND** MAY 继续记录估算 context occupancy，但 SHALL NOT 将其标记为实际 run cost

### Requirement: Runtime Telemetry SHALL 观察而不改变执行决策

Runtime Telemetry SHALL 消费 model、context、tool、subagent、compaction 与 governor outcome，并按 model run id 去重；它 SHALL NOT 单独维护会影响控制流的第二套预算或循环计数。详细 token 来源与 caller attribution SHALL 由 `add-agent-context-usage-attribution` change 提供；该 change 未落地时 SHALL 仅保留现有总量观测能力，不得在本 change 建立第二套 usage collector。

#### Scenario: telemetry 关闭

- **WHEN** context display 或外部 tracing 关闭
- **THEN** Agent 的重试、压缩、工具治理和终止行为 SHALL 与开启时一致
