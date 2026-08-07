# agent-hitl Specification

## Purpose

本能力规定 SuperAgent（及将来复用方）的 **Human-in-the-loop**：`HumanInTheLoopMiddleware` 装配、`ask_user` 澄清、沙箱工具审批谓词、超时、以及网页 / 通道 resume 的决策类型对齐。传输事件与落库见 `platform-chat` / `agent-delivery`；路径规范化见 `agent-runtime`。代码：`agent/guardrails/`、`agent/tools/ask_user.py`、`domain/chat/hitl/`。
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
