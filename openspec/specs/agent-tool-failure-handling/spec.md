# agent-tool-failure-handling Specification

## Purpose

本能力规定 Agent 工具调用的**双层语义**：调用层（invoke）失败分类与用户可见脱敏文案；执行层（outcome）表达命令退出、超时、空输出等「工具已返回」场景；以及在此之上的**权威生命周期 `state`**（流式、snapshot 与历史恢复共用）。权威实现位于 `noesis.errors.tool_failure`（分类与短文案）与 `noesis.chat.tool_state`（state 推导与进程结果提取）；前端 `tool-output-available` 展示契约见 `platform-chat` 规格。

## Requirements

### Requirement: 本规格 SHALL 限定适用范围

本规格 **SHALL** 仅约束同时满足下列条件的工具结束路径：

1. 上游为 LangChain `astream_events` 的 `on_tool_end` / `on_tool_error`；
2. 经事件映射层发出 `tool-output-available`；
3. 工具调用链装配了 `ToolFailureMiddleware`（经 `create_noesis_agent` 的 Agent 路径）。

**In scope**：`COMMON_QA`、`FAULT_OPERATION_QA`、`SUPER_AGENT_QA` 的 ReAct Agent 工具调用。**Out of scope**：`TEST_CASE_QA` 的 CaseCoordinator（产出 `phase-*` 自定义事件，不受本规格约束）；整轮 SSE `error`（由 `sanitize_stream_error` 处理，见分流 Requirement）；前端 UI 标签与占位文案（权威来源：`platform-chat`）。

#### Scenario: 测试用例流不走双层模型

- **WHEN** `qa_type=TEST_CASE_QA` 且 `CaseCoordinator` 产出 `phase-start`
- **THEN** 该路径 **SHALL NOT** 要求 `outcome` 或 `errorCategory` 字段

### Requirement: 系统 SHALL 区分调用层 status 与执行层 outcome

对 **in-scope** 的每次工具结束，系统 SHALL 拆为两层语义：

| 层 | 字段 | 取值 | 含义 |
|----|------|------|------|
| 调用层 | `status` | `success` \| `error` | 工具处理器是否**未抛异常**地返回（对齐 LangGraph `ToolMessage.status`） |
| 执行层 | `outcome` | 见下表 | **仅当** `status=success` 时评估 |

`outcome` 枚举（互斥，**仅** `status=success`）：`ok`（有可展示正文且进程类 `exit_code == 0` 且未超时）、`empty`（调用成功但无可展示正文）、`command_failed`（进程类 `exit_code != 0` 且未超时）、`timed_out`（返回体中 `timed_out == true`，调用仍成功）。

**超时双轨（有意区分，不得混用字段）**：工具边界抛超时异常 → `status=error`、`errorCategory=execution_timeout`、SHALL NOT 携带 `outcome`；进程类工具正常返回 `timed_out: true` → `status=success`、`outcome=timed_out`、SHALL NOT 使用 `errorCategory=execution_timeout`。当 `status=error` 时系统 **SHALL NOT** 写入 `outcome`。

`state` 推导与进程结果提取 SHALL 集中于 `noesis.chat.tool_state`（`derive_tool_state` / `extract_process_result`）。

#### Scenario: 工具边界超时走异常轨

- **WHEN** `execute` 边界抛出 `ToolTimeoutError`
- **THEN** SSE SHALL `status=error`、`errorCategory=execution_timeout`、用户 `error` 为「执行超时」
- **AND** SHALL NOT 含 `outcome`

#### Scenario: 进程类超时走结果轨

- **WHEN** 进程类工具返回 `{ "timed_out": true, ... }` 且未抛异常
- **THEN** SSE SHALL `status=success`、`outcome=timed_out`
- **AND** SHALL NOT 含 `errorCategory`

#### Scenario: execute 命令非零退出

- **WHEN** `execute` 返回 `{ "stderr": "not found", "exit_code": 127, "timed_out": false }` 且未抛异常
- **THEN** `status=success`、`outcome=command_failed`、`exit_code=127`
- **AND** Agent `ToolMessage` SHALL 保留完整 JSON

### Requirement: SSE 与落库字段 SHALL 使用 snake_case

`tool-output-available` 的 `data:` JSON 与 assistant `content.parts` 中 tool part **SHALL** 对新增语义字段使用 **snake_case**（`duration_ms`、`outcome`、`exit_code`、`timed_out`、`truncated`、`output`）；失败分类历史键 `errorCategory` 保持 camelCase 不变。**SHALL NOT** 引入 `exitCode`、`timedOut`、`durationMs` 等 camelCase 变体。

#### Scenario: SSE 与落库 exit_code 一致

- **WHEN** 事件映射发出 `outcome=command_failed` 且 `exit_code=2`
- **THEN** 落库 part SHALL 含 `exit_code: 2`（整数）

### Requirement: classify_tool_failure SHALL 为单一分类入口

`classify_tool_failure`（`noesis.errors.tool_failure`）SHALL 仅在 `status=error` 路径使用，分类优先级：

1. `ToolFailureError`（含子类）→ 携带的 `category`；
2. 异常链（`__cause__` 优先、`__context__` 补充，有界节点数）按异常类型与 `errno` 映射——类型优先，禁止对自由文本做正则推断；
3. `raw` 以 `[tool_error category=...]` 开头 → 解析头；
4. `task` 工具的失败文案前缀 → `subagent_failure`（仅文案兜底，不读 builder 状态）；
5. 其它 → `unknown`。

异常链得到非 `unknown` 结果时 SHALL NOT 被 `raw` 文本覆盖。`invalid_arguments` SHALL 优先使用结构化提取的摘要（如 pydantic 校验错误首行），而非异常全文。**SHALL NOT** 在分类器内访问消息 builder、子图 parts 或 SSE 上下文。

#### Scenario: 连接被拒绝

- **WHEN** 抛出 `httpx.ConnectError`
- **THEN** `errorCategory=network_unreachable`

#### Scenario: 自由文本不得误分类

- **WHEN** `status=error` 且 content 为 `HTTP 403 Forbidden in response body`，无类型化异常
- **THEN** `errorCategory=unknown`，SHALL NOT 为 `permission_denied`

#### Scenario: 参数错误给结构化摘要

- **WHEN** 工具入参校验抛 pydantic 异常
- **THEN** 用户可见 `error` SHALL 为校验错误首行等结构化摘要
- **AND** SHALL NOT 携带 pydantic dump 全文（input_value 回显、文档链接等噪声）

### Requirement: 用户 error 短句 SHALL 由类别映射并可读细节

用户可见 `error` 文案 SHALL 由 `_CATEGORY_TEXTS` 类别映射生成（`network_unreachable`→连接失败、`network_timeout`→网络超时、`execution_timeout`→执行超时、`infrastructure`→环境暂时不可用、`permission_denied`→没有执行权限、`tool_not_found`→工具不存在、`cancelled`→已停止）；可重试类别 SHALL 统一追加后缀提示（防模型盲目重试）。`invalid_arguments`、`subagent_failure` 与 `unknown` SHALL 使用脱敏后的细节首行（剥 `Error:` 前缀、有界截断），**SHALL NOT** 暴露堆栈或内部路径；排障细节由 `errorCategory` 与服务端日志 `tool_failure_detail` 承载。执行层 `command_failed` / `timed_out`（结果轨）**SHALL NOT** 使用 `error` 字段。

#### Scenario: 命令失败无 error 字段

- **WHEN** `status=success`、`outcome=command_failed`
- **THEN** part `error` SHALL 缺省

#### Scenario: 可重试失败带后缀

- **WHEN** 分类结果为可重试的 `infrastructure`
- **THEN** 用户 `error` SHALL 为「环境暂时不可用」+ 稍后重试语义后缀

### Requirement: 工具失败类型归属 harness

工具失败分类与异常类型的权威实现 SHALL 位于 `noesis.errors.tool_failure`。平台 SSE/Delivery 映射层 SHALL 直接使用该路径，**SHALL NOT** re-export 或维护第二份互斥分类表。

#### Scenario: 导入路径

- **WHEN** middleware 或 web tools 抛出基础设施失败
- **THEN** SHALL 使用 `noesis.errors.tool_failure.ToolInfrastructureError`

### Requirement: 工具边界 SHALL 显式抛出 ToolFailureError

`noesis.agents.tools`、MCP 包装与沙箱 backend 在能确定调用失败原因时 **SHALL** 抛 `ToolFailureError` 子类；允许 `raise ... from <typed_exc>`。禁止将超时/基础设施错误伪装为 success 空输出。

#### Scenario: 沙箱不可用

- **WHEN** 沙箱 runner 未就绪
- **THEN** `ToolInfrastructureError`；用户 `error`「环境暂时不可用」

### Requirement: 整轮流错误与单 tool 错误 SHALL 分流脱敏

系统 SHALL 对整轮 SSE `error` 与单 tool `error` 使用不同脱敏函数，**SHALL NOT** 混用分类器：`sanitize_tool_error` → `classify_tool_failure`，用于 tool `error`；`sanitize_stream_error`（`noesis.chat.event_mapping.failure_notice`）→ 整轮 SSE `error`。

#### Scenario: 整轮 infrastructure 脱敏

- **WHEN** 整轮 `error` detail 为 `[INTERNAL_ERROR] Docker image not found`
- **THEN** 用户文案为兜底脱敏短句，不含内部路径

### Requirement: 调用失败 SHALL 转为可续推的 error ToolMessage

工具调用异常（LangGraph 控制异常与整轮取消除外）SHALL 使用权威分类规则生成与原 tool call id 配对的 `status=error` ToolMessage。通用异常翻译 SHALL NOT 同时管理 run budget、subagent scope、telemetry 或 artifact lifecycle。LangGraph 控制异常与整轮取消 SHALL 原样传播；若会话随后恢复，message canonicalization SHALL 在再次调用模型前修复不完整配对。

#### Scenario: Typed Failure 可续推

- **WHEN** MCP、Web、KB 或 filesystem tool 抛出 `ToolFailureError`
- **THEN** 系统 SHALL 返回同 tool call id 的 error ToolMessage
- **AND** 模型 SHALL 能在合法 tool call/result history 上继续决策

### Requirement: 工具 SHALL 具有跨流式与历史一致的权威生命周期状态

每个 in-scope 工具 part SHALL 持久化 `state`，取值仅为 `running | approval_pending | succeeded | failed | timed_out | rejected | cancelled`。`running` 与 `approval_pending` 为非终态，其余为终态；终态一经写入 SHALL NOT 被晚到或重复事件改回非终态（`can_transition_tool_state` 单一裁决）。既有调用层 `status` 与执行层 `outcome` 继续保留，`state` 作为 UI、snapshot 与历史恢复的权威生命周期：异常轨超时与结果轨 `outcome=timed_out` 的 `state` 均 SHALL 为 `timed_out`。

#### Scenario: 网络调用异常

- **WHEN** `web_fetch` 抛出分类为 `network_unreachable` 的调用异常
- **THEN** tool part SHALL 为 `status=error`、`state=failed`、`errorCategory=network_unreachable`
- **AND** SSE、Run snapshot 与 assistant 落库 SHALL 一致

#### Scenario: 命令非零退出

- **WHEN** `execute` 正常返回 `exit_code=127` 且 `timed_out=false`
- **THEN** tool part SHALL 为 `status=success`、`outcome=command_failed`、`state=failed`
- **AND** 用户 SHALL NOT 看到"已完成"或"运行中"

### Requirement: HITL SHALL 使用明确的等待、拒绝与恢复状态

需要用户确认的 action part SHALL 从 `running` 转为 `approval_pending`；批准后 SHALL 回到 `running` 并等待真实工具结果；拒绝后 SHALL 进入 `rejected`。HITL 状态 SHALL NOT 通过普通 `running` 标签表达。

#### Scenario: 等待执行授权

- **WHEN** Run 发出包含 execute action 的 `hitl-required`
- **THEN** 对应 execute part SHALL 为 `state=approval_pending`
- **AND** UI SHALL 显示"等待确认"并允许用户操作

#### Scenario: 用户拒绝

- **WHEN** 用户对 action 提交 reject
- **THEN** 对应 part SHALL 进入 `state=rejected`
- **AND** 刷新后 SHALL 继续显示"已拒绝"

### Requirement: Run 边界 SHALL 收敛所有非终态工具

Run 进入 `completed | partial | error | interrupted` 等真正终态前，系统 SHALL reconcile assistant 树中所有工具 part，终态 snapshot 与历史消息 SHALL NOT 含 `running` 或 `approval_pending`。HITL 暂停不是真正终态，但除本次 interrupt 对应 action 与承载该 interrupt 的父 task 外，不得遗留无执行者的 `running` part。

#### Scenario: 用户停止正在执行的工具

- **WHEN** 用户停止 Run 且 assistant 中仍有 `running` tool part
- **THEN** 该 part SHALL 在 Run 终态前转为 `cancelled`
- **AND** assistant 历史 SHALL NOT 显示其仍在运行

#### Scenario: HITL 与并行工具同时出现

- **WHEN** 事件映射已处理并行工具的结束事件后收到 `hitl-required`
- **THEN** 已结束工具 SHALL 保留真实终态
- **AND** action tool SHALL 为 `approval_pending`
- **AND** 无后续执行者的其它非终态 tool SHALL 收敛为 `cancelled`

### Requirement: 工具终态事件与投影 SHALL 保留完整机器字段

事件映射层、RunEvent、`RunProjection`、消息 builder、PersistSink 与 Run snapshot SHALL 完整保留 `state`、`status`、`outcome`、`errorCategory`、`exit_code`、`timed_out`、`duration_ms`、`truncated` 中适用的字段。任何中间层 SHALL NOT 因重建 part 而丢弃已知终态或降级为 `running`。

#### Scenario: 失败后刷新

- **WHEN** 实时 SSE 已收到 `web_fetch state=failed`，随后用户刷新页面
- **THEN** 历史或 Run snapshot 中同一 `tool_call_id` SHALL 仍为 `failed`
- **AND** 前端 SHALL 以服务端状态 replace 客户端缓存

#### Scenario: 重复终态事件

- **WHEN** 同一 `tool_call_id` 的相同终态因重放再次到达
- **THEN** builder SHALL 幂等保持一条 part
- **AND** SHALL NOT 生成重复工具卡片

### Requirement: 工具失败 SHALL 对用户可见且不泄露内部信息

任一工具进入 `failed | timed_out | rejected | cancelled` 时，用户 SHALL 在对应工具卡片看到明确状态；展开态 SHALL 提供安全短句、适用的退出码/用户输出与下一步建议。系统 SHALL NOT 向用户展示堆栈、宿主路径、provider 名称、私有网络解析结果或密钥。有可见最终回答时 SHALL NOT 仅因某次工具失败显示回答级完整性提示；无可见回答时 SHALL 提供可重试操作。

#### Scenario: 部分网页来源失败但回答继续

- **WHEN** 一个 `web_fetch` 失败而其它来源成功，Agent 最终仍生成回答
- **THEN** 失败卡片 SHALL 显示"连接失败"
- **AND** 回答底部 SHALL NOT 额外显示结果不完整提示

#### Scenario: 环境内部错误脱敏

- **WHEN** 工具日志包含容器名、内部路径或异常堆栈
- **THEN** 用户卡片 SHALL 仅显示安全文案
- **AND** 完整技术细节 SHALL 只写入服务端日志

### Requirement: 进程工具 SHALL 以结构化结果判定终态

进程结果提取 SHALL 只读取显式结构化字段（`exit_code` / `timed_out` / `truncated`，见 `extract_process_result`）与 execute 输出携带的命令结果标记行，不得根据自由文本包含 `failed`、`not found` 等词猜测终态。用户显式提供 shell 容错表达式时 SHALL 遵循 shell 最终退出码。

#### Scenario: 系统生成命令找不到可执行文件

- **WHEN** 系统生成的命令因可执行文件不存在返回 exit code 127
- **THEN** outcome SHALL 为 `command_failed` 且 state SHALL 为 `failed`
- **AND** 用户展开详情 SHALL 看到安全 stderr 与退出码 127

#### Scenario: 用户显式容错命令

- **WHEN** 用户明确执行 `optional-command || true` 且 shell 最终返回 0
- **THEN** 系统 SHALL 按最终 exit code 记录执行成功
- **AND** SHALL NOT 仅因 stdout/stderr 文本含错误词而改判失败

### Requirement: 工具结果有界化 SHALL 不改变原始 Outcome

工具正文被预算中间件或 artifact 机制截断时，`status`、`errorCategory`、`outcome`、exit code 与 timed-out 语义 SHALL 保持不变。Tool Execution SHALL 使用处理后的 ToolMessage content，SHALL NOT 通过解析第三方提示文案重建 artifact 字段。

#### Scenario: command_failed 输出被有界化

- **WHEN** `execute` 返回非零退出且 stdout/stderr 超过单结果预算
- **THEN** result SHALL 仍为 `status=success`、`outcome=command_failed`
- **AND** 有界 content SHALL 保留退出语义与可用的 stderr/stdout

### Requirement: Tool Result Budget SHALL 产生确定性 Replacement

工具结果在写入 effective history 前 SHALL 先由工具源或 Filesystem artifact 机制处理；仍超限时 SHALL 生成包含 artifact path/reference、synopsis、原内容 hash 和 replacement reason 的有界结果。Replacement SHALL 保留原 `status`、`errorCategory`、`outcome` 和 tool call id，并 SHALL 在 checkpoint resume 后重放同一决策。

#### Scenario: 恢复已替换的大结果

- **WHEN** 包含大 ToolMessage 的 run 从 checkpoint 恢复
- **THEN** 有效 history SHALL 继续使用原 replacement record
- **AND** SHALL NOT 重新转存、重新摘要或将 error 改为 success

### Requirement: ToolPart SHALL 持久化 Run evidence 所需的内部 provenance

每个 in-scope 工具调用的后端持久化 ToolPart SHALL 保存稳定 `provider_key`、可选 `provider_version`、结构化 lifecycle state、execution outcome、tool call id、parent/step 关联和受控的 evidence classification，供终态 Run capture、scope 计算、来源追溯和记忆安全门控使用。内置工具 SHALL 使用稳定内置标识；MCP 工具 SHALL 使用不会因展示名称变化而改变的服务标识；无法确定时 SHALL 写入明确 unknown 值。

这些字段是后端内部证据元数据，SHALL NOT 出现在用户可见 SSE、聊天历史 API、前端 tool card、用户错误文案或模型可控制的参数中。任何向客户端序列化 ToolPart 的路径 SHALL 显式剥离内部 provenance、provider 地址和服务端路径。记忆 capture SHALL 使用所有成功/失败/拒绝/超时 ToolPart，不得以 outcome 是否失败决定 Run 是否 eligible。

#### Scenario: MCP 工具记录稳定来源

- **WHEN** 来自某 MCP server 的工具完成
- **THEN** 持久化 ToolPart SHALL 包含该 server 的稳定 provider key 和可得版本
- **AND** SHALL NOT 依赖模型输出或事后扫描当前配置推断历史来源

#### Scenario: 用户可见协议不泄露内部 provenance

- **WHEN** 客户端订阅工具 SSE、刷新历史消息或展开工具卡片
- **THEN** 响应 SHALL NOT 暴露 provider key、provider version、内部 server 名称、网络位置、evidence classification 或服务端路径
