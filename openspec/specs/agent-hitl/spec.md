# agent-hitl Specification

## Purpose
TBD - created by archiving change add-super-agent-hitl. Update Purpose after archive.
## Requirements
### Requirement: HumanInTheLoopMiddleware 装配

当配置 `hitl.enabled=true` 且调用方传入非空 `interrupt_on` 时，`create_noesis_agent` **SHALL** 在运行时防护中间件之前挂载 LangChain `HumanInTheLoopMiddleware`，并 **SHALL** 要求调用方提供 LangGraph `checkpointer`（与 SuperAgent 现有一致）。

`interrupt_on` 未传入或 `hitl.enabled=false` 时，**SHALL NOT** 挂载该中间件，行为与变更前一致。

#### Scenario: 启用 HITL 时挂载中间件

- **WHEN** `create_noesis_agent` 以 `interrupt_on={"execute": True}` 且 `hitl.enabled=true` 创建 Agent
- **THEN** 中间件栈 **SHALL** 包含 `HumanInTheLoopMiddleware`，且 **SHALL** 位于 `ToolErrorHandlingMiddleware` 之前

#### Scenario: 关闭 HITL 时无中间件

- **WHEN** `hitl.enabled=false`
- **THEN** 系统 **SHALL NOT** 挂载 `HumanInTheLoopMiddleware`，无论是否传入 `interrupt_on`

### Requirement: ask_user 澄清工具

系统 **SHALL** 提供名为 `ask_user` 的结构化工具，供模型在任务已启动且缺少关键信息时向用户提问。工具参数 **SHALL** 至少包含：

- `question`（string，必填）：展示给用户的澄清问题；
- `options`（string 数组，可选）：若有则 UI **SHALL** 以选项形式呈现；若无则自由文本回答。

`ask_user` **SHALL** 在 `interrupt_on` 中配置为仅允许 `respond` 决策；**SHALL NOT** 对 `ask_user` 暴露 `approve` 或 `reject`。

用户经 HITL resume 提交的 `respond.message` **SHALL** 作为 `status=success` 的 `ToolMessage` 内容返回模型，**SHALL NOT** 执行其他副作用。

#### Scenario: 模型调用 ask_user 后等待用户回答

- **WHEN** 模型产出对 `ask_user` 的 tool call 且 HITL 已启用
- **THEN** 图 **SHALL** interrupt，且 `hitl-required` 事件的 `kind` **SHALL** 为 `clarification`

#### Scenario: 用户 respond 后继续推理

- **WHEN** 用户对 pending `ask_user` 提交 `{ "type": "respond", "message": "需要 PDF 报告" }`
- **THEN** Agent **SHALL** 收到含该文本的 success ToolMessage 并继续执行，**SHALL NOT** 再次 interrupt 同一 tool call

### Requirement: SuperAgent 沙箱工具审批策略

对 `qa_type=SUPER_AGENT_QA`，系统 **SHALL** 按下列规则决定是否对 tool call 触发 HITL（通过 `InterruptOnConfig.when` 或等价谓词）：

| 工具 | 默认 | 触发 interrupt 条件 |
|------|------|---------------------|
| `read_file` / `ls` / `glob` / `grep` | 放行 | 无 |
| `write_file` / `edit_file` | workspace 放行 | 目标路径规范化后以 `/memory/` 为前缀 |
| `execute` | 常规开发与沙箱内破坏性命令放行 | 网络出口命令、pipe-to-shell（见 design.md D4） |
| `web_search` / `web_fetch` | 放行 | 无 |
| `task` / `write_todos` | 放行 | 无 |

对需审批的 `execute` 与 `/memory/` 写入，**SHALL** 仅允许 `approve` 与 `reject` 决策；**SHALL NOT** 允许 `respond`。

用户 `reject` 后，**SHALL** 向模型返回 error ToolMessage，且 **SHALL NOT** 执行对应命令或写入。

#### Scenario: workspace 内 write_file 不 interrupt

- **WHEN** 模型调用 `write_file` 且 `path` 为 `/notes.md`（非 `/memory/` 前缀）
- **THEN** 系统 **SHALL NOT** 触发 HITL，**SHALL** 直接执行写入

#### Scenario: memory 写入需审批

- **WHEN** 模型调用 `write_file` 且 `path` 为 `/memory/USER.md`
- **THEN** 系统 **SHALL** interrupt 并等待 `approve` 或 `reject`

#### Scenario: 危险 execute 需审批

- **WHEN** 模型调用 `execute` 且 `command` 匹配网络出口或 pipe-to-shell 策略
- **THEN** 系统 **SHALL** interrupt 并等待 `approve` 或 `reject`

#### Scenario: 沙箱内 rm 不审批

- **WHEN** 模型调用 `execute` 且 `command` 为 `rm -rf ./workspace`
- **THEN** 系统 **SHALL NOT** 触发 HITL，**SHALL** 直接执行

#### Scenario: 用户拒绝 execute

- **WHEN** 用户对危险 `execute` 提交 `{ "type": "reject" }`
- **THEN** 命令 **SHALL NOT** 在沙箱中执行，且模型 **SHALL** 收到 error ToolMessage

### Requirement: task-worker 继承 HITL 策略

`SuperAgent` 注册的 `task-worker` 子 Agent **SHALL** 继承主 Agent 的 `interrupt_on` 配置，**SHALL NOT** 因委派而绕过审批或澄清。

#### Scenario: 子 Agent 执行危险命令仍需审批

- **WHEN** `task-worker` 在子上下文中调用匹配策略的 `execute`
- **THEN** 系统 **SHALL** 与主 Agent 相同地触发 HITL

### Requirement: Session grant 语义

对网络类 `execute` 审批，前端 **MAY** 提供 `grant_scope=session`。当用户选择本会话允许且 resume 为 `approve` 时，系统 **SHALL** 在同一会话 `thread_id` 内对同类网络命令跳过后续 interrupt，直至会话结束或沙箱销毁。

对 `/memory/` 写入类审批，**SHALL NOT** 提供 session grant；**SHALL** 每次单独确认。

#### Scenario: 本会话允许网络命令

- **WHEN** 用户对 `curl` 类 `execute` 选择 `approve` 且 `grant_scope=session`
- **THEN** 同会话后续匹配的 `execute` **SHALL NOT** 再次 interrupt，直至会话结束

#### Scenario: memory 写入不可 session grant

- **WHEN** 用户对 `/memory/` 的 `write_file` 审批
- **THEN** UI **SHALL NOT** 展示「本会话允许」选项

### Requirement: HITL 超时

系统 **SHALL** 支持配置 `hitl.ask_timeout_seconds`（默认 86400，即 24 小时）。interrupt 等待超过该时长无 resume 时，**SHALL** 视为对该批 action 的 `reject`，恢复图并按 `platform-chat` 终态规则落库 assistant 消息（可无活跃 SSE 连接）。

#### Scenario: 超时未响应

- **WHEN** `hitl-required` 发出后超过 `ask_timeout_seconds` 无 resume
- **THEN** 系统 **SHALL** 按 reject 处理 pending 工具并终态落库，**SHALL NOT** 无限保留 pending HITL

