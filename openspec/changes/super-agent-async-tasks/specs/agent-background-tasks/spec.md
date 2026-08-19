## ADDED Requirements

### Requirement: 后台子 Agent 执行模型

SuperAgent SHALL 通过进程内 `BackgroundSubagentExecutor` 执行委派子任务：任务在专用守护线程的独立事件循环上运行，生命周期归属 session 而非主 run。`start_task` 工具 SHALL 立即返回 task_id，不阻塞主 Agent。任务状态机 SHALL 覆盖 running / awaiting_approval / completed / failed / cancelled / timed_out；每会话并发上限与单任务超时 SHALL 由 `subagents` 配置约束。进程重启后运行中任务 SHALL 丢失（注册表在内存，接受的设计限制），`check_task` 对不存在的 task_id SHALL 返回可诊断提示。

#### Scenario: 委派后主 Agent 继续

- **WHEN** 主 Agent 调用 `start_task(description)` 且任务启动成功
- **THEN** 工具 SHALL 立即返回 task_id 与「可继续其他工作」提示
- **AND** 主 Agent 本轮 SHALL 不等待子任务完成

#### Scenario: 跨轮收取结果

- **WHEN** 主 Agent 所在 run 已结束，后台任务随后完成
- **THEN** 任务 SHALL 保持在注册表中，任意后续轮次 `check_task(task_id)` SHALL 返回终态与结果

#### Scenario: 并发上限

- **WHEN** 某会话 running + awaiting_approval 任务数已达 `max_concurrent_per_session`
- **THEN** `start_task` SHALL 返回错误说明且不创建任务
- **AND** 其他会话不受影响

#### Scenario: 进程重启

- **WHEN** 后端进程重启后调用 `check_task`
- **THEN** 对重启前启动的 task_id SHALL 返回「任务不存在（可能是进程重启前启动）」类提示，不抛异常

### Requirement: task-worker 编译契约

后台 task-worker SHALL 以独立编译的 LangGraph agent 运行：带 checkpointer（thread_id = task_id）、SUBAGENT profile 中间件栈，且 SHALL NOT 携带后台任务工具自身（禁止递归委派）。task-worker 与主 Agent SHALL 共享同一沙箱 backend（同一 user_id / session_id 的工作区）。

#### Scenario: 递归委派防护

- **WHEN** task-worker 的工具集被组装
- **THEN** 其中 SHALL NOT 出现 start_task / check_task / cancel_task / list_tasks / send_message

### Requirement: Steering 中途调整

系统 SHALL 支持向运行中或待审批的后台任务投递策略调整：指令进入按 task_id 键控的有界队列（上限 10 条，溢出丢最旧）；`SteeringMiddleware` SHALL 在子 Agent 的下一次模型调用边界把待注入指令作为追加 HumanMessage（`[用户策略调整] …`）注入请求，注入即消费。终态任务投递 SHALL 返回错误说明。指令 SHALL 经模型工具 `send_message(task_id, message)` 与用户 API `POST /bg-tasks/{id}/message` 两个入口投递。

#### Scenario: 运行中调整方向

- **WHEN** 任务 running 时投递「聚焦中文源」指令
- **THEN** 子 Agent 下一次模型调用的输入 SHALL 包含该指令对应的 HumanMessage
- **AND** 再下一次模型调用 SHALL NOT 重复出现

#### Scenario: 待审批期间入队

- **WHEN** 任务 awaiting_approval 时投递指令，随后审批通过续跑
- **THEN** 续跑后的首次模型调用 SHALL 注入该指令

#### Scenario: 终态任务投递

- **WHEN** 向 completed / failed / cancelled / timed_out 任务投递指令
- **THEN** SHALL 返回「任务已结束」类错误，不入队

### Requirement: 完成通知注入

后台任务到达终态时 SHALL 向所属会话的待送达通知队列写入一条通知（task_id、终态、结果预览 ≤80 字）。该会话下一次 run 启动组装输入前 SHALL drain 队列并以 `[系统通知]` 前缀注入本轮上下文，注入一次性且 SHALL NOT 写入消息落库内容。系统 SHALL NOT 主动为通知启动 run（无用户消息时不唤醒模型）；前端轮询为用户侧兜底触达。`awaiting_approval` SHALL NOT 注入模型通知（审批触达走用户审批面板）。

#### Scenario: 下一轮收到通知

- **WHEN** 后台任务完成，用户随后发送新消息
- **THEN** 本轮模型输入 SHALL 以 `[系统通知]` 前缀包含该任务完成提示与 check_task 指引
- **AND** 再下一轮 SHALL NOT 重复出现该通知

#### Scenario: 用户消息原文不受污染

- **WHEN** 通知被注入某轮上下文
- **THEN** 该轮用户消息的数据库持久化内容 SHALL 与用户原始输入一致（通知不落库）

#### Scenario: 不主动唤醒

- **WHEN** 后台任务完成但用户未再发消息
- **THEN** 系统 SHALL NOT 自行启动模型调用；前端任务面板 SHALL 经轮询显示终态
