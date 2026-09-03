# agent-hitl Specification

## Purpose

本能力规定 SuperAgent（及将来复用方）的 **Human-in-the-loop**：`HumanInTheLoopMiddleware` 装配、`ask_user` 澄清、沙箱工具审批谓词、超时、以及网页 / 通道 resume 的决策类型对齐。传输事件与落库见 `platform-chat` / `agent-delivery`；路径规范化见 `agent-runtime`。代码：`noesis/agents/guardrails/`、`noesis/agents/tools/ask_user.py`、`noesis/chat/hitl/`。
## Requirements
### Requirement: HumanInTheLoopMiddleware 装配

当 `hitl.enabled=true` 且传入非空 `interrupt_on` 时，`create_noesis_agent` SHALL 挂载 `HumanInTheLoopMiddleware`（位于工具错误处理之前），并 SHALL 要求 checkpointer。`hitl.enabled=false` 或未传 `interrupt_on` 时 **SHALL NOT** 挂载。

#### Scenario: 启用时挂载

- **WHEN** `interrupt_on` 非空且 HITL 开启
- **THEN** 中间件栈 SHALL 含 `HumanInTheLoopMiddleware`

### Requirement: ask_user 澄清工具

系统 SHALL 提供 `ask_user`（`question` 必填，`options` 可选）。HITL 决策 **SHALL** 仅允许 `respond`；**SHALL NOT** 对 `ask_user` 暴露 approve/reject。用户 `respond.message` SHALL 作为 success ToolMessage 返回模型。

#### Scenario: clarification

- **WHEN** 模型调用 `ask_user`
- **THEN** 图 SHALL interrupt，且 `hitl-required.kind` SHALL 为 `clarification`

### Requirement: SuperAgent 审批策略（少问）

SuperAgent SHALL 按下表决定工具调用默认放行或中断审批：

| 工具 | 默认 | interrupt 条件 |
|------|------|----------------|
| 读类文件系统工具 | 放行 | 无 |
| `write_file` / `edit_file` | workspace 放行 | 规范化路径以 `/memory/` 为前缀 |
| `execute` | 放行 | 网络出口命令、pipe-to-shell |
| `web_*` / `task` / `write_todos` | 放行 | 无 |

需审批的调用 **SHALL** 仅允许 `approve` / `reject`。路径规范化 SHALL 经 `canonicalize_agent_path`（例：``/workspace/notes.md``，**不是**虚拟根 ``/notes.md``）。

会话级「本会话放行」网络 execute 授权 MAY 由 grant 机制提供（与网页 / Telegram 按钮对齐）。

#### Scenario: workspace 写入不审批

- **WHEN** 写入 `/workspace/notes.md`
- **THEN** **SHALL NOT** 因路径触发 HITL

#### Scenario: 记忆写入要审批

- **WHEN** 写入 `/memory/AGENTS.md`
- **THEN** SHALL interrupt，待 approve/reject

### Requirement: 超时

pending HITL 超时后 SHALL 按配置 reject 或失败终态，**SHALL NOT** 永久卡在 streaming。

#### Scenario: 超时结束

- **WHEN** pending 超过超时阈值
- **THEN** run SHALL 进入可观测终态（reject/error），assistant **SHALL NOT** 永久 streaming

### Requirement: 多端 resume

网页 `hitl/resume` 与通道（如 Telegram callback / 下一条文字 respond）SHALL 映射到同一决策模型（approve / reject / respond / 会话放行）。通道出站 HITL 提示 SHALL 不依赖浏览器 SSE。

#### Scenario: Telegram 批准

- **WHEN** 用户点击 Telegram 批准按钮且 pending 有效
- **THEN** 系统 SHALL resume 同一 run / `assistant_message_id`

### Requirement: 飞书 HITL 操作 SHALL 映射到统一决策模型
需要审批时，系统 SHALL 向原飞书会话发送只含安全摘要的交互卡片；approve/reject action SHALL 映射到网页和 Telegram 共用的 decision 模型。回调 token SHALL 为短期、不透明、单次使用，并校验用户、通道、session 与 pending 状态。

#### Scenario: 已配对用户批准有效卡片
- **WHEN** 已配对用户点击未过期审批卡片的批准按钮
- **THEN** 系统 SHALL 以 `approve` resume 原 Agent run
- **AND** 后续输出 SHALL 继续投递到同一飞书会话

#### Scenario: 其他用户点击审批卡片
- **WHEN** 非 pending 所属用户提交相同 callback token
- **THEN** 系统 SHALL 拒绝 resume 且 SHALL NOT 执行待审批工具

### Requirement: 飞书 clarification SHALL 接受下一条已配对文本
当 pending decision 要求补充信息时，系统 SHALL 将同一绑定用户的下一条文本映射为 `respond`，并 SHALL 避免同时创建新的独立 Agent run。

#### Scenario: 用户补充缺失信息
- **WHEN** 会话存在有效 clarification pending 且所属用户发送文本
- **THEN** 系统 SHALL 使用该文本 resume 原 run
- **AND** SHALL NOT 为该文本新建第二个 run

### Requirement: 后台子 Agent 工具审批

后台 task-worker 带 `interrupt_on` 编译；遇审批工具时 LangGraph SHALL 落 checkpoint 并 interrupt，executor SHALL 捕获 `__interrupt__` 将任务转为 `awaiting_approval` 并保留审批载荷（interrupt_id / action_requests）。审批与停止 SHALL 复用标准 run API（`/runs/{run_id}/hitl/resume`、`/runs/{run_id}/stop`）：决策以 `Command(resume={"decisions": [...]})` 在同一 thread 续跑，与主 run HITL 的 resume 契约一致。非 awaiting_approval 状态提交决策 SHALL 报错。审批超时（`hitl.ask_timeout_seconds`）SHALL 自动按拒绝续跑。审批触达 SHALL 经 child session 事件流（approval 事件）与目录状态，不依赖主 run 存活；详情抽屉复用主 Agent 的审批卡组件。

#### Scenario: 后台任务触发审批

- **WHEN** 后台子 Agent 调用需审批工具且任务暂停于 interrupt
- **THEN** 任务状态 SHALL 为 awaiting_approval，审批载荷 SHALL 含该工具调用的 name / args / tool_call_id
- **AND** 主 run（若已结束）不受影响；目录与详情抽屉 SHALL 展示审批卡

#### Scenario: 批准续跑

- **WHEN** 用户批准
- **THEN** 决策 SHALL 以 `Command(resume={"decisions": [{"type": "approve"}]})` 在同一 thread 续跑
- **AND** 任务回到 running 直至终态

#### Scenario: 拒绝

- **WHEN** 用户拒绝并附说明
- **THEN** 子 Agent SHALL 收到拒绝结果并继续推理（可改道或汇报），任务最终到达终态

#### Scenario: 审批超时

- **WHEN** 任务 awaiting_approval 超过 `hitl.ask_timeout_seconds`
- **THEN** executor SHALL 自动按拒绝续跑（不挂起不失败），任务最终到达终态

#### Scenario: 越权访问

- **WHEN** 用户 A 对用户 B 的后台任务提交决策或查询
- **THEN** SHALL 返回 404 语义（不泄露存在性）

### Requirement: HITL 决策应用 SHALL 为单一投影实现

approve / reject / respond 决策对消息投影的应用（工具 part 状态翻转、reject 的拒绝 ToolMessage 投影、respond 的应答投影）SHALL 由单一投影实现提供，主聊天、通道与后台子 Agent 复用同一实现，SHALL NOT 在各链路保留各自的决策应用拷贝。后台子 Agent 的审批拒绝/应答 SHALL 与主链路同构地在子会话消息投影中合成工具终态展示（被拒工具在子会话详情中 SHALL 有明确的拒绝态，而非停留在无终态）。

#### Scenario: 子会话被拒工具有终态展示

- **WHEN** 用户拒绝后台子 Agent 的某次工具调用并附说明
- **THEN** 子会话详情的该工具 part SHALL 展示拒绝状态与说明
- **AND** 渲染形态 SHALL 与主聊天被拒工具一致

#### Scenario: 决策应用单一实现

- **WHEN** 代码审查发现主聊天 / 通道 / 子 Agent 任一链路的决策投影逻辑
- **THEN** 其 SHALL 委托同一投影函数实现
- **AND** SHALL NOT 存在语义重复的并行拷贝

### Requirement: HITL 超时语义与文案 SHALL 两侧一致

主聊天 run 与后台子 Agent run 的审批超时 SHALL 语义一致：超时自动按拒绝续跑（同一决策归一化路径）、assistant SHALL NOT 永久停留在 streaming，超时注入的用户可见文案 SHALL 为同一份。

#### Scenario: 子 Agent 审批超时与主链路同文案

- **WHEN** 后台子 Agent awaiting_approval 超过 `hitl.ask_timeout_seconds`
- **THEN** 系统 SHALL 自动按拒绝续跑并注入与主链路相同的超时提示文案
- **AND** 子会话消息 SHALL 到达可观测终态

