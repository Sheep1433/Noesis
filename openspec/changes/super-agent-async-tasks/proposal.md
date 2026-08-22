# Proposal: SuperAgent 全异步后台子 Agent（执行 / 审批 / 续话 / 查看）

> 最终产品模型已修订为“子 Agent 会话化”：请以
> [`docs/architecture/subagent-sessions.md`](../../../docs/architecture/subagent-sessions.md)
> 和 design.md 顶部的最终决策为准。本文后续的 BgTask 描述是过渡期实现记录，迁移完成后不再保留
> subagent 专用任务详情协议。

## Why

SuperAgent 原用 deepagents `SubAgentMiddleware` 的同步 `task` 工具委派子任务：主 Agent 阻塞等待子 Agent 跑完才能继续。参考业界成熟 Agent 产品（Claude Code 的后台 Agent + 通知唤醒 + SendMessage、通用 agent harness 的 `run_in_background` 参数化委派）后确认，纯同步委派有结构性问题：主上下文被拖住、无法跨轮收结果、无法中途调整、看不到子 Agent 过程。

初版设计把同步委派整体退役、只留全异步。评审后修正：**依赖链场景（查 X 才能做 Y）需要前台等待**，正确解法不是二选一，而是**单工具 + `run_in_background` 参数**（默认后台；下一步依赖结果时模型选前台），选择权归模型且不存在双工具混乱。同时确认 followup-turn（子会话追加 turn）语义优于中途注入式 steering——子 Agent 带全部历史作为完整新轮次接续推理；「点开查看」应以子会话完整记录（而非步骤摘要轮询）为主。

## What Changes

- **执行层（已完成）**：`BackgroundSubagentExecutor`——专用守护线程独立事件循环 + 进程级注册表 + 六态状态机 + 每会话并发上限 + 任务超时；worker 经 async 工厂在隔离 loop 内惰性编译（LLM 客户端 / checkpointer 连接池绑定隔离 loop）；任务生命周期归属 session，主 run 结束不回收。
- **HITL（已完成）**：task-worker 带 checkpointer + interrupt_on 编译；审批工具触发 interrupt 落 checkpoint 转 `awaiting_approval`，审批经 `Command(resume={"decisions": [...]})` 同 thread 续跑；审批超时按拒绝续跑。API + 前端 BgTaskPanel 审批卡。
- **过程捕获（已完成）**：executor 经 `astream(values)` 记录有界步骤摘要（≤50 条），并在每个新增步骤发布会话级 `progress` SSE；前端据 `progress_count` 实时刷新已展开任务的完整子会话。
- **单工具 + `run_in_background`（本变更，修订）**：`start_task` 增加参数——默认 `run_in_background=true`（立即返回，现行为）；`false` 为**前台等待**：工具阻塞至本任务终态并把结果作为工具返回值（前台模式），供依赖链委派使用。不再有独立同步委派路径。
- **followup-turn 续话（本变更，修订，替代 steering 注入）**：`send_message(task_id, message)` 语义从「注入当前轮模型调用」改为「子会话追加一个 turn」——运行中任务：消息排队，当前 turn 结束后 executor 链式 `ainvoke(HumanMessage)` 同 thread 开新 turn；completed 任务：冷恢复同 thread 开新 turn（结果更新，任务回到 running）。failed / timed_out / cancelled 不可续。SteeringMiddleware 退役（其语义被 followup-turn 覆盖）。
- **子会话查看（本变更）**：后台任务的子 Agent 完整消息历史天然持久在 checkpointer thread（`thread_id = task_id`）；新增 `GET /bg-tasks/{id}/messages` 读取 thread 历史消息，前端任务卡「查看详情」打开子会话抽屉渲染完整过程（模型轮次 + 工具调用 + 结果），作为过程展示的主入口；收起态仅展示由 SSE 实时更新的紧凑概览。
- **Prompt 语义（已完成，随修订微调）**：委派章节教模型——独立可并行任务**一起 start 后继续干活**；**下一步依赖结果时 `run_in_background=false` 前台等待**；收到 `[系统通知]` 后 `check_task` 收果；方向跑偏 `send_message` 追加指示。
- **完成通知（已完成）**：终态写会话级待送达队列，下一次 run 组装输入时一次性 `[系统通知]` 前缀注入（不落库）；continuation run 的 user 消息带 `source_kind=bg_task_notice` 标记，前端渲染为系统通知条而非用户气泡。
- **后台命令（本变更）**：`execute` 工具加 `run_in_background` 参数（默认 false，前台行为零变化）——true 时命令作为 `kind="shell"` 任务进现有注册表/状态机/通知/面板管线（不经 worker 编译，直接 backend 执行）；长命令不再靠 timeout=0 阻塞等待。
- **已知限制（接受）**：注册表与通知队列在内存，进程重启丢运行中任务与未送达通知（子会话历史因在 checkpointer 仍保留）；通知不主动唤醒 run，前端轮询兜底。

## Capabilities

### New Capabilities

- `agent-background-tasks`: 后台子 Agent 的执行模型、状态机、单工具同异步参数、followup 续话、子会话读取、完成通知与进程生命周期。

### Modified Capabilities

- `agent-hitl`: 增加后台任务审批通道（interrupt 转 awaiting_approval、decisions 续跑、超时拒绝、前端审批卡）。
- `agent-profiles`: SuperAgent 委派语义改为单工具 `run_in_background` 按依赖选同异步 + 通知收果；同步 `SubAgentMiddleware` 的 `task` 工具退役。

## Impact

- 后端：`noesis.agents.subagents`（executor / followup 队列 / tools / 子会话读取）、`super_agent.py` 装配与 prompt（execute 工具替换挂载点）、`services/bg_task_service.py`、`server/api/chat_api.py`、`config subagents` 段、lifespan。
- 前端：`BgTaskPanel`（审批卡 + 概览 + 子会话抽屉 + shell 任务卡）、api/chat.ts、chat.vue（事件流 + 系统通知条）。
- 测试：executor 契约扩展（前台等待 / followup 链式 turn / 冷恢复 / 子会话读取 / shell 任务生命周期）；steering 中间件测试随退役删除。
- 非目标：通知主动唤醒 run、跨进程任务注册表持久化（重启恢复运行态）、FaultOperationAgent 迁移、远程 Agent Protocol 执行、文件系统工具（ls/read/write/edit/glob/grep）与 backend 接口改造。
