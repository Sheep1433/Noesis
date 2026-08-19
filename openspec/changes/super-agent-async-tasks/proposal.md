# Proposal: SuperAgent 全异步后台子 Agent（执行 / 审批 / 调整 / 通知）

## Why

SuperAgent 原用 deepagents `SubAgentMiddleware` 的同步 `task` 工具委派子任务：主 Agent 阻塞等待子 Agent 跑完才能继续。对照 deer-flow（进程内后台执行 + 后端轮询）与 Claude Code（后台 Agent + 完成通知推送 + SendMessage 调整），同步委派有三个结构性问题：

1. **主上下文被拖住**：重子任务跑多久，主 Agent 就阻塞多久；用户得不到响应。
2. **无法跨轮收结果**：任务生命周期绑定在主 run 内，主 run 结束任务即消失。
3. **无法中途调整**：子 Agent 跑偏方向时只能等它跑完或取消重来。

本变更把 SuperAgent 委派改为**进程内全异步**（不经 langgraph-api），并在执行层之上补齐使异步真正可用的三块：模型会用（prompt 语义）、能调整（steering）、知道完成（通知）。

## What Changes

- **执行层（已完成）**：`BackgroundSubagentExecutor`——专用守护线程独立事件循环 + 进程级注册表 + 六态状态机（running / awaiting_approval / completed / failed / cancelled / timed_out）+ 每会话并发上限 + 任务超时；任务生命周期归属 session，主 run 结束不回收。
- **工具面（已完成）**：`start_task` / `check_task` / `cancel_task` / `list_tasks` 暴露给主 Agent；`start_task` 立即返回 task_id。
- **HITL（已完成）**：task-worker 带 checkpointer + interrupt_on 编译；审批工具触发 interrupt 落 checkpoint 转 `awaiting_approval`，审批经 `Command(resume={"decisions": [...]})` 同 thread 续跑；审批超时按拒绝续跑。API + 前端 BgTaskPanel 审批卡。
- **Prompt 语义（本变更）**：SuperAgent system prompt 的委派章节从「调用 task 等结果」改写为「start 后继续工作、收到系统通知再 check」；明确依赖链不委派、并行独立子线各自 start。
- **Steering（本变更）**：`send_message(task_id, message)`——主 Agent 或用户向运行中/待审批的后台任务投递策略调整；经 `SteeringMiddleware` 在子 Agent **下一次模型调用边界**注入为追加 HumanMessage，消费即清空。
- **完成通知（本变更）**：后台任务到达终态时写入会话级待送达通知；该会话**下一次 run 启动时**注入系统通知前缀（「后台任务 bg-xxx 已完成，用 check_task 收取」）；prompt 教模型收到通知后收结果，**不主动反复 check**。
- **同步 task 工具退役**：SuperAgent 栈不再挂 `SubAgentMiddleware`；FaultOperationAgent 暂保留同步委派（短链路运维、结果即时要用，属不同 Agent 的独立选择，非同模型双轨）。
- **已知限制（接受）**：注册表与通知队列在内存，进程重启丢运行中任务与未送达通知；通知不主动唤醒 run（无用户消息时模型不被调用），前端 BgTaskPanel 轮询兜底。

## Capabilities

### New Capabilities

- `agent-background-tasks`: 后台子 Agent 的执行模型、状态机、工具面、steering 注入、完成通知与进程生命周期。

### Modified Capabilities

- `agent-hitl`: 增加后台任务审批通道（interrupt 转 awaiting_approval、decisions 续跑、超时拒绝、前端审批卡）。
- `agent-profiles`: SuperAgent 委派语义改为异步 start/check + 通知收果；同步 task 工具从 SUPER_AGENT_QA 退役。

## Impact

- 后端：`noesis.agents.subagents`（executor / steering / tools）、`super_agent.py` 装配与 prompt、`services/bg_task_service.py`、`services/qa/service.py`（通知注入点）、`config subagents` 段、lifespan。
- 前端：`BgTaskPanel`（已有审批卡 + 状态列表）、api/chat.ts、chat.vue 轮询。
- 测试：`test_bg_subagent_executor.py` 扩展 steering / 通知契约；super_agent prompt 断言更新。
- 非目标：主动唤醒 run（无用户消息时启动模型调用）、跨进程任务持久化（重启恢复）、FaultOperationAgent 迁移、远程 Agent Protocol 执行。
