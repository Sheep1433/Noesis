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
