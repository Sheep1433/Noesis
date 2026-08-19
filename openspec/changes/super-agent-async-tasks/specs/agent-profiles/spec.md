## MODIFIED Requirements

### Requirement: SUPER_AGENT_QA / SuperAgent

`SUPER_AGENT_QA` SHALL 装配 SuperAgent：会话工作区、`/skills/public|personal`、`/memory/`、web 工具、后台子 Agent 工具面（`start_task` / `check_task` / `cancel_task` / `list_tasks` / `send_message`）、可选 HITL。提示词 SHALL 指引使用 `/workspace/...` 绝对路径，**SHALL NOT** 再教虚拟根 `/notes.md`。SuperAgent 栈 SHALL NOT 挂同步 `SubAgentMiddleware` 的 `task` 工具（同步委派已退役）。

提示词委派语义 SHALL 为单工具按依赖选同异步：独立可并行的子任务**一起 `start_task`（默认后台）后继续当前工作或直接回复用户**；**下一步动作依赖该结果**时用 `run_in_background=false` 前台等待。收到 `[系统通知]` 后用 `check_task` 收取结果；**SHALL NOT** 指引启动后立即反复 check；追加指示或后续工作用 `send_message`（子任务追加一个 turn）；需在主上下文连续推理且不可隔离的链 SHALL 留在主上下文。

工作区内研究产出约定目录为 `workspace/research/`（Agent 路径 `/workspace/research/...`），**SHALL NOT** 将其建模为独立 virtual root `/research/`。

SuperAgent 主 Agent SHALL 使用与 COMMON_QA 相同的普通 Markdown citation 规则。子 Agent 返回给主 Agent 的研究小结 SHOULD 保留原始来源 URL。系统 SHALL NOT 为不同模型维护 citation provider allowlist。

#### Scenario: Skills 路径

- **WHEN** SuperAgent 读取平台 skill 文件
- **THEN** 路径 SHALL 形如 `/skills/public/{name}/SKILL.md`

#### Scenario: 研究笔记

- **WHEN** 模型写入研究报告
- **THEN** 目标 SHOULD 为 `/workspace/research/...` 下文件

#### Scenario: 异步委派不阻塞

- **WHEN** 主 Agent 以默认参数调用 `start_task` 委派重子任务
- **THEN** 本轮 SHALL 继续执行后续步骤或直接回复用户，不等待子任务

#### Scenario: 依赖结果选前台

- **WHEN** 主 Agent 的下一步动作依赖子任务结果
- **THEN** 提示词 SHALL 指引用 `run_in_background=false`，工具返回终态文本

#### Scenario: 通知驱动的收果

- **WHEN** 模型输入包含 `[系统通知] 后台任务 … 已完成`
- **THEN** 模型 SHALL 用 `check_task` 收取结果并汇总给用户

#### Scenario: 主 Agent 汇总 Web 调研结果

- **WHEN** SuperAgent 使用主 Agent 或子 Agent 返回的 Web 来源生成最终报告
- **THEN** 最终回答 SHALL 保持普通文本流式输出，不启用 citation structured response
