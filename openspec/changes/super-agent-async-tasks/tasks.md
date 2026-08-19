# Tasks: SuperAgent 全异步后台子 Agent

> 基线（executor / 工具面 / HITL 审批 / API / BgTaskPanel）已在 `feat/async-subagent` 完成并勾选；剩余为 prompt 语义、steering、完成通知三块闭环。

## 1. 执行层基线（已完成）

- [x] 1.1 `BackgroundSubagentExecutor`：隔离守护线程事件循环 + 进程级注册表 + 六态状态机 + 会话并发上限 + 任务超时 watchdog + 审批超时自动 reject 续跑
- [x] 1.2 `build_background_task_tools`：start_task / check_task / cancel_task / list_tasks，start 立即返回 task_id
- [x] 1.3 `_compile_task_worker` 直接编译 task-worker（checkpointer + interrupt_on，不含后台工具自身防递归）；super_agent 弃用 SubAgentMiddleware
- [x] 1.4 config `subagents` 段（max_concurrent_per_session / task_timeout_seconds）+ lifespan 退出清理
- [x] 1.5 回归测试 `test_bg_subagent_executor.py`（含真实 LangGraph 图 interrupt 暂停 → approve/reject 续跑闭环）

## 2. HITL 审批面（已完成）

- [x] 2.1 interrupt 捕获转 `awaiting_approval`（payload 含 interrupt_id / action_requests）
- [x] 2.2 `submit_decisions` 经 `Command(resume={"decisions": [...]})` 同 thread 续跑
- [x] 2.3 BgTaskService（归属校验）+ API：GET /sessions/{id}/bg-tasks、POST /bg-tasks/{id}/decisions、POST /bg-tasks/{id}/cancel
- [x] 2.4 前端 BgTaskPanel：待审批卡（批准/拒绝）+ 任务状态列表；会话切换拉取 + 活跃任务 5s 轮询

## 3. Prompt 语义改写

- [x] 3.1 重写 SuperAgent prompt `<task_delegation>` / `<approach>` 委派章节：start 即返回、启动后继续工作或回复用户、依赖链不委派、并行独立子线各自 start
- [x] 3.2 收果语义：收到 `[系统通知]` 后 check_task 收取；禁止启动后立刻反复 check；中途可 list_tasks / send_message
- [x] 3.3 更新 prompt 回归测试（关键词断言），删除对旧同步 task 语义的断言

## 4. Steering（中途调整）

- [x] 4.1 `steering.py`：模块级 registry（dict[task_id, deque] 线程安全，上限 10 溢出丢最旧）+ `put / drain / reject-if-terminal` API；executor 挂接 `send_message`
- [x] 4.2 `SteeringMiddleware`：wrap_model_call 按 thread_id drain 指令，追加 `HumanMessage("[用户策略调整] …")` 后 override request.messages；消费即清空
- [x] 4.3 task-worker 栈挂载 SteeringMiddleware（仅 SUBAGENT profile）；awaiting_approval 期间入队、resume 后首个模型调用生效的契约测试
- [x] 4.4 模型工具 `send_message(task_id, message)`；用户侧 API `POST /bg-tasks/{id}/message`（BgTaskService 归属校验）
- [ ] 4.5 BgTaskPanel 运行中任务卡加「发送指令」输入（可选增强，验收不阻塞）

## 5. 完成通知

- [x] 5.1 executor 终态回调写会话级待送达通知队列（进程内；含 task_id、终态、结果预览 ≤80 字）
- [x] 5.2 `exec_query` 组装本轮输入前 drain 通知，拼 `[系统通知]` 前缀（不落库、只注入一次）；多任务终态合并为一条通知
- [x] 5.3 单测：通知一次性消费、不污染用户消息原文（DB 内容断言）、无通知时零开销

## 6. 收口

- [x] 6.1 后端全量回归 + 前端 lint/build；确认无新增失败（基线 11 个既有失败除外）
- [ ] 6.2 手动验收脚本：start → 主 Agent 继续回复 → send_message 调整 → 任务完成 → 下一轮收到系统通知 → check_task 收果；含审批路径（后台任务触发审批 → 面板批准 → 续跑完成）
- [ ] 6.3 归档准备：spec delta 并入 `agent-background-tasks` / `agent-hitl` / `agent-profiles` 主规格
