## REMOVED Requirements

### Requirement: 系统 SHALL 对工具失败进行统一分类

**Reason**: 原规格将「工具失败」等同于 `status=error`，未覆盖命令非零退出、空输出、执行超时(success 路径)等场景；分类规则并入双层模型的「调用层」章节。

**Migration**: 调用失败分类规则保留于新规格同名 Requirement，并增加 `status=error` 前置条件。

### Requirement: 工具边界 SHALL 通过 ToolFailureError 显式分类

**Reason**: 补充沙箱不得吞超时/基础设施异常为 success 的硬性要求，合并入新规格。

**Migration**: 实现方对照新规格「进程类工具」与「工具边界」两节改造 `AioSandboxBackend`。

### Requirement: 整轮流错误与单 tool 错误 SHALL 分流脱敏

**Reason**: 行为不变，移入新规格同名章节。

**Migration**: 无代码变更。

### Requirement: 工具失败 SHALL 转为可续推的 error ToolMessage

**Reason**: 措辞改为「调用失败」；行为不变。

**Migration**: 无代码变更。

### Requirement: 子 Agent 工具失败 SHALL 可向上归类

**Reason**: 移入新规格同名章节，行为不变。

**Migration**: 无代码变更。

### Requirement: LLM 可读详情与用户可见文案 SHALL 分离

**Reason**: 扩展至 `status=success` + `outcome != ok` 场景的双通道规则。

**Migration**: 前端依据 `outcome` 展示标签，不再将 stderr 映射为 `error` 字段。

## ADDED Requirements

### Requirement: 系统 SHALL 区分调用层 status 与执行层 outcome

系统在 Agent 运行时（经 `create_noesis_agent` 装配的全部 `qa_type` 路径）中，SHALL 将每次工具结束事件拆为两层语义：

| 层 | 字段 | 取值 | 含义 |
|----|------|------|------|
| 调用层 | `status` | `success` \| `error` | 与 LangGraph `ToolMessage.status` 对齐：工具处理器是否**未抛异常**地返回 |
| 执行层 | `outcome` | 见下表 | 仅当 `status=success` 时评估；描述业务执行结果 |

`outcome` 枚举（互斥）：`ok`、`empty`、`command_failed`、`timed_out`。

当 `status=error` 时，系统 SHALL NOT 要求携带 `outcome`；用户侧以 `error` + `errorCategory` 为准。

**关键区分**：调用失败（`status=error`）与调用成功但执行异常（`status=success` + `outcome != ok`）SHALL 使用不同 SSE 字段集；后者 SHALL NOT 强制升级为 `status=error`。

`ToolOutcome` 解析 SHALL 集中于 `backend/domain/chat/streaming/tool_outcome.py`。

#### Scenario: execute 命令非零退出仍为 success

- **WHEN** `execute` 返回 JSON 含 `exit_code: 127` 且 `timed_out: false` 且未抛异常
- **THEN** SSE `tool-output-available` SHALL 含 `status=success`、`outcome=command_failed`、`exitCode=127`

#### Scenario: execute 无输出但成功退出

- **WHEN** `execute` 返回 `exit_code: 0` 且 stdout/stderr 均为空
- **THEN** SSE SHALL 含 `status=success`、`outcome=empty`
- **AND** 前端 SHALL 展示「（无输出）」

#### Scenario: 沙箱超时走调用失败

- **WHEN** `AioSandboxBackend.execute` 超时并抛出 `ToolTimeoutError`
- **THEN** SSE SHALL 含 `status=error`、`errorCategory=execution_timeout`
- **AND** SHALL NOT 产出 `outcome=empty` 的 success 帧

### Requirement: 进程类工具 SHALL 返回可解析的结构化结果

`execute` 及等价远程命令工具在调用成功时，SHALL 使 bridge 能解析 `exit_code`、`timed_out`、`stdout`、`stderr`、`truncated`。

`AioSandboxBackend.execute` 超时 SHALL 抛出 `ToolTimeoutError`；基础设施异常 SHALL 抛出 `ToolInfrastructureError`；SHALL NOT 以空 success 响应掩盖。

#### Scenario: 沙箱超时不得返回空 success

- **WHEN** AIO `shell.exec_command` 因 `timeout` 超时
- **THEN** SHALL 抛出 `ToolTimeoutError`，middleware 产出 `status=error`

### Requirement: LangGraphSseBridge SHALL 解析 outcome 并写入 SSE

`on_tool_end` 且 `status=success` 时，SHALL 调用 `parse_tool_outcome` 并写入可选字段 `outcome`、`exitCode`、`timedOut`、`truncated`。

#### Scenario: web_fetch 空正文

- **WHEN** `web_fetch` 成功返回空字符串
- **THEN** SSE SHALL 含 `outcome=empty`

#### Scenario: 错误帧不携带 outcome

- **WHEN** `status=error` 且 `errorCategory=network_unreachable`
- **THEN** SSE SHALL NOT 要求 `outcome` 字段

### Requirement: 工具调用场景目录 SHALL 作为验收矩阵

主规格 `openspec/specs/agent-tool-failure-handling/spec.md` 中 **SHALL** 维护完整场景目录，按 **S-**（success/outcome）、**E-**（errorCategory）、**N-**（网络/超时辨析）、**T-**（task/subagent）、**P-**（并行/续推）、**F-**（整轮流）、**A-**（误分类反例）编号。实现单测与 golden **SHALL** 以该目录为覆盖率检查表。

#### Scenario: 场景目录覆盖调用成功与软异常

- **WHEN** 审查 `agent-tool-failure-handling` 规格场景目录
- **THEN** SHALL 含 S-01～S-11（含 `ok`、`empty`、`command_failed`、`timed_out`、`truncated`）

#### Scenario: 场景目录覆盖十类调用失败

- **WHEN** 审查场景目录第二节
- **THEN** SHALL 含 E-01～E-15，覆盖全部 `ToolFailureCategory` 枚举值

#### Scenario: 场景目录覆盖子 Agent 与整轮流分流

- **WHEN** 审查场景目录第四、六节
- **THEN** SHALL 含 T-01～T-05 与 F-01～F-03

#### Scenario: 误分类反例纳入单测

- **WHEN** 运行 `test_tool_failure` / `test_tool_errors` 或等价套件
- **THEN** SHALL 覆盖 A-01～A-06 所列反例
