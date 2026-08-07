## ADDED Requirements

### Requirement: 工具 SHALL 具有跨流式与历史一致的权威生命周期状态

每个 in-scope 工具 part SHALL 持久化 `state`，取值仅为 `running | approval_pending | succeeded | failed | timed_out | rejected | cancelled`。`running` 与 `approval_pending` 为非终态，其余为终态；终态一经写入 SHALL NOT 被晚到或重复事件改回非终态。既有调用层 `status` 与执行层 `outcome` 继续保留，`state` 作为 UI、snapshot 与历史恢复的权威生命周期。

#### Scenario: 网络调用异常

- **WHEN** `web_fetch` 抛出分类为 `network_unreachable` 的调用异常
- **THEN** tool part SHALL 为 `status=error`、`state=failed`、`errorCategory=network_unreachable`
- **AND** SSE、Run snapshot 与 assistant 落库 SHALL 一致

#### Scenario: 命令非零退出

- **WHEN** `execute` 正常返回 `exit_code=127` 且 `timed_out=false`
- **THEN** tool part SHALL 为 `status=success`、`outcome=command_failed`、`state=failed`
- **AND** 用户 SHALL NOT 看到“已完成”或“运行中”

#### Scenario: 两种超时映射为同一用户状态

- **WHEN** 工具边界抛出执行超时，或进程工具正常返回 `outcome=timed_out`
- **THEN** 两条路径的 `status/outcome` SHALL 保留各自语义
- **AND** 两条路径的 `state` 均 SHALL 为 `timed_out`

### Requirement: HITL SHALL 使用明确的等待、拒绝与恢复状态

需要用户确认的 action part SHALL 从 `running` 转为 `approval_pending`；批准后 SHALL 回到 `running` 并等待真实工具结果；拒绝后 SHALL 进入 `rejected`。HITL 状态 SHALL NOT 通过普通 `running` 标签表达。

#### Scenario: 等待执行授权

- **WHEN** Run 发出包含 execute action 的 `hitl-required`
- **THEN** 对应 execute part SHALL 为 `state=approval_pending`
- **AND** UI SHALL 显示“等待确认”并允许用户操作

#### Scenario: 用户拒绝

- **WHEN** 用户对 action 提交 reject
- **THEN** 对应 part SHALL 进入 `state=rejected`
- **AND** 刷新后 SHALL 继续显示“已拒绝”

### Requirement: Run 边界 SHALL 收敛所有非终态工具

Run 进入 `completed | partial | error | interrupted` 等真正终态前，系统 SHALL reconcile assistant 树中所有工具 part，终态 snapshot 与历史消息 SHALL NOT 含 `running` 或 `approval_pending`。HITL 暂停不是真正终态，但除本次 interrupt 对应 action 与承载该 interrupt 的父 task 外，不得遗留无执行者的 `running` part。

#### Scenario: 用户停止正在执行的工具

- **WHEN** 用户停止 Run 且 assistant 中仍有 `running` tool part
- **THEN** 该 part SHALL 在 Run 终态前转为 `cancelled`
- **AND** assistant 历史 SHALL NOT 显示其仍在运行

#### Scenario: HITL 与并行工具同时出现

- **WHEN** bridge 已处理并行工具的结束事件后收到 `hitl-required`
- **THEN** 已结束工具 SHALL 保留真实终态
- **AND** action tool SHALL 为 `approval_pending`
- **AND** 无后续执行者的其它非终态 tool SHALL 收敛为 `cancelled`

#### Scenario: 父 task 含等待授权的子工具

- **WHEN** 子 Agent 内 execute 等待授权
- **THEN** execute 与承载本次 interrupt 的父 task MAY 为 `approval_pending`
- **AND** 其它已失败子工具 SHALL 保持 `failed`，不得被父 task 状态覆盖

### Requirement: 工具终态事件与投影 SHALL 保留完整机器字段

`LangGraphSseBridge`、RunEvent、`RunProjection`、`AssistantMessageBuilder`、PersistSink 与 Run snapshot SHALL 完整保留 `state`、`status`、`outcome`、`errorCategory`、`exit_code`、`timed_out`、`duration_ms`、`truncated` 中适用的字段。任何中间层 SHALL NOT 因重建 part 而丢弃已知终态或降级为 `running`。

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
- **THEN** 失败卡片 SHALL 显示“连接失败”
- **AND** 回答底部 SHALL NOT 额外显示结果不完整提示

#### Scenario: 环境内部错误脱敏

- **WHEN** 工具日志包含容器名、内部路径或异常堆栈
- **THEN** 用户卡片 SHALL 仅显示“环境不可用”或等价安全文案
- **AND** 完整技术细节 SHALL 只写入服务端日志

### Requirement: 进程工具 SHALL 以结构化结果判定终态

Noesis 控制的 `execute/bash` 包装 SHALL 返回真实 `exit_code`、`timed_out`、stdout 与 stderr，且 SHALL NOT 通过追加 `|| true` 吞掉系统生成命令的失败。状态解析 SHALL 只读取结构化字段，不得根据自由文本包含 `failed`、`not found` 等词猜测终态。用户显式提供 shell 容错表达式时 SHALL 遵循 shell 最终退出码。

#### Scenario: 系统生成命令找不到可执行文件

- **WHEN** 系统生成的命令因可执行文件不存在返回 exit code 127
- **THEN** outcome SHALL 为 `command_failed` 且 state SHALL 为 `failed`
- **AND** 用户展开详情 SHALL 看到安全 stderr 与退出码 127

#### Scenario: 用户显式容错命令

- **WHEN** 用户明确执行 `optional-command || true` 且 shell 最终返回 0
- **THEN** 系统 SHALL 按最终 exit code 记录执行成功
- **AND** SHALL NOT 仅因 stdout/stderr 文本含错误词而改判失败
