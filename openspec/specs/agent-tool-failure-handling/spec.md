# agent-tool-failure-handling

## Purpose

本能力规定 Agent 工具调用的**双层语义**：调用层（invoke）失败分类与用户可见脱敏文案；执行层（outcome）表达命令退出、超时、空输出等「工具已返回」场景。后端分类与解析供 middleware、SSE 桥接、落库共用；**前端 `tool-output-available` 展示契约见 `platform-chat` 规格**。

## Requirements

### Requirement: 本规格 SHALL 限定适用范围

本规格 **SHALL** 仅约束同时满足下列条件的工具结束路径：

1. 上游为 LangChain `astream_events` 的 `on_tool_end` / `on_tool_error`；
2. 经 `LangGraphSseBridge` 发出 `tool-output-available`；
3. 工具调用链装配了 `ToolErrorHandlingMiddleware`（经 `create_noesis_agent` 的 Agent 路径）。

**In scope（示例）**：`COMMON_QA`、`FAULT_OPERATION_QA`、`SUPER_AGENT_QA` 的 ReAct Agent 工具调用。

**Out of scope（显式排除）**：

- `TEST_CASE_QA` 的 `CaseCoordinator` / `build_test_case_graph`：产出 `phase-*` 等自定义事件，**不**产出 `tool-output-available`，**不受**本规格 outcome / `errorCategory` 约束；
- 整轮 SSE `error` / `abort`（由 `failure_notice.sanitize_stream_error` 处理，见下文分流 Requirement）；
- 前端 UI 标签与占位文案（权威来源：`platform-chat`）。

#### Scenario: 测试用例流不走双层模型

- **WHEN** `qa_type=TEST_CASE_QA` 且 `CaseCoordinator.run_agent` 产出 `phase-start`
- **THEN** 该路径 **SHALL NOT** 要求 `outcome` 或 `errorCategory` 字段

#### Scenario: 深度研究 execute 在范围内

- **WHEN** `SUPER_AGENT_QA` 经 `create_noesis_agent` 调用 `execute` 且 bridge 处理 `on_tool_end`
- **THEN** 本规格 **SHALL** 适用

### Requirement: 系统 SHALL 区分调用层 status 与执行层 outcome

对 **in-scope** 的每次工具结束，系统 SHALL 拆为两层语义：

| 层 | 字段 | 取值 | 含义 |
|----|------|------|------|
| 调用层 | `status` | `success` \| `error` | 工具处理器是否**未抛异常**地返回（对齐 LangGraph `ToolMessage.status`） |
| 执行层 | `outcome` | 见下表 | **仅当** `status=success` 时评估 |

`outcome` 枚举（互斥，**仅** `status=success`）：

| `outcome` | 含义 |
|-----------|------|
| `ok` | 有可展示用户正文，且（进程类）`exit_code == 0` 且 `timed_out != true` |
| `empty` | 调用成功但无可展示正文（stdout/stderr/文本均为空或仅空白） |
| `command_failed` | 进程类：`exit_code != 0` 且 `timed_out != true` |
| `timed_out` | 进程类：工具返回体中 `timed_out == true`（**调用仍成功**） |

**超时双轨（有意区分，不得混用字段）**：

| 路径 | `status` | 机器字段 | 说明 |
|------|----------|----------|------|
| A：工具边界抛 `ToolTimeoutError` / 读超时等 | `error` | `errorCategory=execution_timeout` | Agent 收到 error `ToolMessage` |
| B：MCP `bash` 等返回 `timed_out: true` | `success` | `outcome=timed_out` | Agent 收到 success + 结构化 JSON |

路径 A **SHALL NOT** 携带 `outcome`；路径 B **SHALL NOT** 使用 `errorCategory=execution_timeout`。

当 `status=error` 时，系统 **SHALL NOT** 写入 `outcome`；用户侧以 `error` + `errorCategory` 为准。

**Non-Goal**：本规格 **不** 统一上述双轨的监控埋点或报表口径；运维统计 MAY 在日志层将 `execution_timeout` 与 `outcome=timed_out` 映射为同一业务标签，**不在** SSE/part 协议层合并。

`ToolOutcome` 解析与用户展示格式化 SHALL 集中于 `backend/domain/chat/streaming/tool_outcome.py`；调用失败分类 SHALL 集中于 `tool_failure.py`（`ToolFailureCategory` 于 `tool_errors.py`）。

#### Scenario: 沙箱超时走路径 A

- **WHEN** `AioSandboxBackend.execute` 抛出 `ToolTimeoutError`
- **THEN** SSE SHALL `status=error`、`errorCategory=execution_timeout`、用户 `error` 为「执行超时」
- **AND** SHALL NOT 含 `outcome`

#### Scenario: MCP bash 超时走路径 B

- **WHEN** MCP `bash` 返回 `{ "timed_out": true, ... }` 且未抛异常
- **THEN** SSE SHALL `status=success`、`outcome=timed_out`、`timed_out=true`
- **AND** SHALL NOT 含 `errorCategory`

#### Scenario: execute 命令非零退出

- **WHEN** `execute` 返回 `{ "stderr": "not found", "exit_code": 127, "timed_out": false }` 且未抛异常
- **THEN** `status=success`、`outcome=command_failed`、`exit_code=127`
- **AND** Agent `ToolMessage` SHALL 保留完整 JSON

### Requirement: SSE 与落库字段 SHALL 使用 snake_case

`tool-output-available` 的 `data:` JSON 与 assistant `content.parts` 中 tool part **SHALL** 对新增语义字段使用 **snake_case**，与既有 `duration_ms`、`tool_call_id`、`error_category`（若落库用 snake_case 则写作 `error_category`；SSE 现有实现为 `errorCategory` 时 **保持 camelCase 仅该键**，见下表）对齐：

| 字段 | SSE JSON 键 | 落库 part 键 | 说明 |
|------|-------------|--------------|------|
| 耗时 | `duration_ms` | `duration_ms` | 已有 |
| 失败分类 | `errorCategory` | `errorCategory` | 已有 camelCase，保持不变 |
| 执行结果 | `outcome` | `outcome` | 新增 |
| 退出码 | `exit_code` | `exit_code` | 新增 |
| 超时标记 | `timed_out` | `timed_out` | 新增 |
| 截断标记 | `truncated` | `truncated` | 新增 |
| 用户可见正文 | `output` | `output` | 见「用户 output 格式化」Requirement |

**SHALL NOT** 引入 `exitCode`、`timedOut`、`durationMs` 等 camelCase 变体。

#### Scenario: SSE 与落库 exit_code 一致

- **WHEN** bridge 发出 `outcome=command_failed` 且 `exit_code=2`
- **THEN** 落库 part SHALL 含 `exit_code: 2`（整数）

### Requirement: classify_tool_failure SHALL 仅负责调用失败分类

`classify_tool_failure(exc, *, raw, tool_name)`（`tool_failure.py`）**SHALL** 仅在 `status=error` 路径使用。分类优先级：

1. `ToolFailureError`（含子类）→ `category`；
2. 对 `exc` 沿 `__cause__`（优先）、`__context__`（若与 cause 不同）剥链，**最多检查 4 个异常节点**（非「图深度」，而是 BFS 访问节点数上限），对每个节点仅按 `type(node)` 与 `errno` 映射；
3. `raw` 以 `[tool_error category=...]` 开头 → 解析头；
4. `tool_name == "task"` 且 `raw` 以 `Task failed.` / `Task timed out` 开头 → `subagent_failure`（**仅**文案兜底，**不**读 builder 状态）；
5. 其它 → `unknown`。

**SHALL NOT** 在 `classify_tool_failure` 内访问 `AssistantMessageBuilder`、子图 parts 或 SSE 上下文。子图 error tool 判定 **SHALL** 仅由 Bridge 层完成（见下条 Requirement）。

步骤 2 得 `unknown` 时继续 3～4；步骤 2 得非 `unknown` 时 **SHALL NOT** 被 `raw` 覆盖 category。

#### Scenario: 连接被拒绝

- **WHEN** 抛出 `httpx.ConnectError`
- **THEN** `errorCategory=network_unreachable`

#### Scenario: 自由文本不得误分类

- **WHEN** `status=error` 且 content 为 `HTTP 403 Forbidden in response body`，无类型化异常
- **THEN** `errorCategory=unknown`，SHALL NOT 为 `permission_denied`

#### Scenario: 深层 cause 链在节点上限内可映射

- **WHEN** `RuntimeError` → `WrapperError` → `httpx.ConnectError`（共 3 节点）且均在剥链上限内
- **THEN** `errorCategory=network_unreachable`

### Requirement: LangGraphSseBridge SHALL 负责 outcome 解析与子 Agent 汇总

Bridge 在 `on_tool_end` **SHALL**：

**Success 路径**（`ToolMessage.status != error` 且无调用失败异常）：

1. 调用 `parse_tool_outcome(tool_name, raw_output)`（**仅**对进程类工具名白名单 `execute`、`bash` 解析 `exit_code` / `timed_out`；其它工具按纯文本 empty/ok）；
2. **SHALL** 写入 `outcome`（新消息不得省略；见缺省归一化）及可选 `exit_code`、`timed_out`、`truncated`；
3. 调用 `format_user_tool_output(raw_output, outcome)` 生成用户可见 `output`（**非**向用户透传原始 JSON，见专条 Requirement）；
4. `builder` 侧 `append_tool_output` 的模型用字段 **SHALL** 保留 `raw_output` 全文。

**Error 路径**：

1. 调用 `classify_tool_failure` 得 `error` / `errorCategory`；
2. **SHALL NOT** 写入 `outcome`；
3. 用户 `output` SHALL 为空字符串。

**`task` 工具（Bridge 专节，依赖 builder 状态）**：

在调用 `classify_tool_failure` 之前，若 `tool_name == "task"`：

1. 若 `_task_has_subagent_tool_error(builder, task_call_id)` 为真（扫描 `builder` 内 `parent_task_call_id` 匹配且 `status=error` 的嵌套 parts）→ `subagent_failure`；
2. 否则若 output 匹配 `classify_task_tool_output` 失败前缀 → `subagent_failure`；
3. 否则按普通工具处理。

**事件顺序假设**：嵌套 tool 的 `on_tool_end` **SHALL** 在对应 `task` 的 `on_tool_end` 之前进入 builder（LangGraph 默认嵌套完成顺序）。若漏标，**MAY** 在 `bridge.finalize()` 对仍为 `running`/`success` 的 task parts 做一次 reconcile 扫描；本规格不强制二次 reconcile，但单测 **SHALL** 覆盖「子 tool 先落库」主路径。

#### Scenario: 子图含 error tool 标 subagent_failure

- **WHEN** `task` 的 `on_tool_end` 时 builder 已有嵌套 `web_fetch` part 为 `status=error`
- **THEN** task part SHALL `status=error`、`errorCategory=subagent_failure`

#### Scenario: web_fetch 空正文

- **WHEN** 非进程类 `web_fetch` 返回 `""`
- **THEN** `status=success`、`outcome=empty`

#### Scenario: 错误帧无 outcome

- **WHEN** `errorCategory=network_unreachable`
- **THEN** SHALL NOT 含 `outcome`

### Requirement: 进程类工具白名单与结构化返回 SHALL 限定解析范围

系统 SHALL 维护进程类工具名白名单（`PROCESS_TOOL_NAMES`：`execute`、`bash`），并仅对其实施结构化 outcome 解析。

其它工具（含 MCP 返回 JSON 的 RAG、检索、自定义工具）**SHALL** 按纯文本处理：`trim` 后非空 → `outcome=ok`，否则 → `outcome=empty`；**SHALL NOT** 因 JSON 内误含 `exit_code` 字段而 `command_failed`。

`AioSandboxBackend.execute`：

- 超时 → 抛 `ToolTimeoutError`（路径 A）；
- 基础设施错误 → 抛 `ToolInfrastructureError`；
- 正常 → `ExecuteResponse`，由上层 `execute` 工具序列化为 JSON 字符串。

#### Scenario: RAG JSON 不误判 command_failed

- **WHEN** `hybrid_search` 返回 `{"exit_code":0,"chunks":[...]}`（非进程类工具名）
- **THEN** `outcome=ok`（有可读文本时），SHALL NOT `command_failed`

#### Scenario: 沙箱超时不得空 success

- **WHEN** docker / local_shell `execute` 超时
- **THEN** SHALL 抛 `ToolTimeoutError`，SHALL NOT `status=success` + `outcome=empty`

### Requirement: 用户 output SHALL 与 Agent 原始输出分离格式化

系统 SHALL 按 `outcome` 为用户 `output`（SSE/part）与 Agent `ToolMessage` 内容采用不同格式化策略。

对 `status=success`：

| `outcome` | 用户 `output`（SSE/part） | Agent / `ToolMessage` |
|-----------|---------------------------|------------------------|
| `ok` / `empty` | `format_user_tool_output`：纯文本或「（无输出）」占位 | 原始工具返回全文 |
| `command_failed` | 优先 `stderr`，其次 `stdout`，再拼接 `退出码: {exit_code}`；均空则仅「退出码: N」 | 原始 JSON |
| `timed_out` | 已有 stdout/stderr 正文 + 固定行「执行已超时」 | 原始 JSON |

对 `status=error`：用户 `output` SHALL 为空；技术详情仅在 Agent `ToolMessage.content`。

`format_user_tool_output` **SHALL** 与 `parse_tool_outcome` 同置于 `tool_outcome.py`。

#### Scenario: S-05 用户见 stderr 非 JSON

- **WHEN** `execute` 返回 JSON 含 `stderr: "command not found"`、`exit_code: 127`
- **THEN** 用户 `output` SHALL 含 `command not found` 与 `退出码: 127`
- **AND** Agent 侧 SHALL 仍为完整 JSON 字符串

### Requirement: outcome 缺省归一化 SHALL 兼容历史消息

系统 SHALL 对无 `outcome` 字段的历史 tool part 按下列规则归一化展示语义：

| 条件 | 归一化 `outcome` |
|------|------------------|
| 新流式消息，`status=success` | bridge **SHALL** 显式写入 `outcome` |
| 历史 part：有 `outcome` | 直接使用 |
| 历史 part：无 `outcome`、`status=success`、有非空 `output` | 前端视为 `ok` |
| 历史 part：无 `outcome`、`status=success`、无 `output` | 前端视为 `empty` 并展示「（无输出）」 |
| `status=error` | 不读 `outcome` |

#### Scenario: 旧消息无 outcome 有空 output

- **WHEN** 加载历史 tool part：`status=success`、有 `output`、无 `outcome`
- **THEN** 前端 SHALL 按 `ok` 渲染，SHALL NOT 崩溃

### Requirement: 工具边界 SHALL 显式抛出 ToolFailureError

`agent/tools/*`、MCP 包装、`AioSandboxBackend` 在能确定调用失败原因时 **SHALL** 抛 `ToolFailureError` 子类；允许 `raise ... from <typed_exc>`。禁止将超时/基础设施伪装为 success 空输出。

#### Scenario: 沙箱不可用

- **WHEN** 沙箱 runner 未就绪
- **THEN** `ToolInfrastructureError`；用户 `error`「环境不可用」

### Requirement: 整轮流错误与单 tool 错误 SHALL 分流脱敏

系统 SHALL 对整轮 SSE `error` 与单 tool `error` 使用不同脱敏函数，**SHALL NOT** 混用分类器。

- `sanitize_tool_error` → `classify_tool_failure`，用于 tool `error`；
- `sanitize_stream_error` → 整轮 SSE `error`，**SHALL NOT** 委托 tool 分类器。

#### Scenario: 整轮 infrastructure 脱敏

- **WHEN** 整轮 `error` detail 为 `[INTERNAL_ERROR] Docker image not found`
- **THEN** 用户文案「环境不可用」

### Requirement: 调用失败 SHALL 转为可续推的 error ToolMessage

`ToolErrorHandlingMiddleware` **SHALL** 捕获工具调用异常（`GraphBubbleUp` 除外）并转为 `status=error` 的 `ToolMessage`，content 含 `[tool_error category=...]`。**Non-Goal**：SSE/part **不**携带 `retryable`；`retryable` **仅**出现在 Agent 侧 `[tool_error ... retryable=...]` 供模型决策。

#### Scenario: GraphBubbleUp 不被吞掉

- **WHEN** 抛出 `GraphBubbleUp`
- **THEN** middleware 原样重抛

### Requirement: 用户 error 短句 SHALL 固定且刻意粗粒度

系统 SHALL 对 `status=error` 的用户可见 `error` 使用下表固定短句（**SHALL NOT** 暴露堆栈或内部路径）：

| `errorCategory` | 用户 `error` | 说明 |
|-----------------|-------------|------|
| `network_unreachable`, `network_timeout` | 连接失败 | |
| `execution_timeout` | 执行超时 | 路径 A |
| `invalid_arguments` | 参数错误 | |
| `infrastructure` | 环境不可用 | 脱敏 |
| `cancelled` | 已停止 | |
| `tool_not_found`, `permission_denied`, `subagent_failure`, `unknown` | 执行失败 | **有意**不暴露细分类，避免泄露内部路径/权限细节；排障用 `errorCategory`（支持人员可见）与日志 `tool_failure_detail` |

执行层 `command_failed` / `timed_out`（路径 B）**SHALL NOT** 使用 `error` 字段；UI 由 `platform-chat` 按 `outcome` 展示。

#### Scenario: 命令失败无 error 字段

- **WHEN** `status=success`、`outcome=command_failed`
- **THEN** part `error` SHALL 缺省

### Requirement: 工具调用场景目录 SHALL 作为验收矩阵

实现与单测 **SHALL** 以本节为覆盖率基准。字段命名遵循 snake_case Requirement；UI 行为遵循 `platform-chat`。

**调用成功（S-）**

| ID | 要点 | `status` | `outcome` |
|----|------|----------|-----------|
| S-01 | 文本工具有内容 | success | ok |
| S-02 | 文本工具空 | success | empty |
| S-03 | execute exit=0 有 stdout | success | ok |
| S-04 | execute exit=0 无输出 | success | empty |
| S-05 | execute exit=127 有 stderr | success | command_failed |
| S-06 | stderr 含 Permission denied | success | command_failed（非 permission_denied 类） |
| S-07 | MCP bash `timed_out:true` | success | timed_out |
| S-08 | truncated + exit=0 | success | ok |
| S-09 | truncated + exit≠0 | success | command_failed |
| S-10 | hybrid_search 命中 | success | ok |
| S-11 | 非进程类 JSON 含 exit_code | success | ok/empty（不走进程规则） |

**调用失败（E-）**

| ID | errorCategory | 用户 error |
|----|---------------|------------|
| E-01 | network_unreachable | 连接失败 |
| E-02 | network_timeout | 连接失败 |
| E-03 | execution_timeout | 执行超时 |
| E-04 | invalid_arguments | 参数错误 |
| E-05 | tool_not_found | 执行失败 |
| E-06 | permission_denied | 执行失败 |
| E-07 | infrastructure | 环境不可用 |
| E-08 | subagent_failure（Bridge 子图） | 执行失败 |
| E-09 | subagent_failure（Task 文案兜底） | 执行失败 |
| E-10 | task 成功 | success / ok |
| E-11 | cancelled | 已停止 |
| E-12 | unknown | 执行失败 |
| E-13 | cause 链 ConnectError | network_unreachable |
| E-14 | ToolMessage error 无头 | middleware 重写 |
| E-15 | on_tool_error | 同 error 帧 + duration_ms |

**网络辨析（N-）**：N-01 ConnectTimeout→network_timeout；N-02 ReadTimeout→execution_timeout；N-03 ConnectError→network_unreachable。

**子 Agent（T-）**：T-01 全成功；T-02 嵌套网络失败→task subagent_failure；T-03 嵌套 command_failed 但 task 仍 success；T-04 parentTaskCallId UI；T-05 Task timed out 文案。

**并行（P-）**：P-01 并行不错位；P-02 GraphBubbleUp；P-03 unknown 可续推。

**整轮流（F-）**：F-01 sanitize_stream_error；F-02 整轮 infrastructure；F-03 单 tool 错误不必然整轮 error。

**反例（A-）**：A-01 403 文案→unknown；A-02 stderr permission→非 category；A-03 日志 tool not found→unknown；A-04 配置 timeout 字样→不推断；A-05 无 cause RuntimeError→unknown；A-06 command_failed 不用 error 字段。

#### Scenario: 场景目录覆盖 E-03 与 S-07 超时双轨

- **WHEN** 单测断言 E-03 与 S-07
- **THEN** E-03 SHALL 仅 `status=error` + `execution_timeout`；S-07 SHALL 仅 `status=success` + `outcome=timed_out`
