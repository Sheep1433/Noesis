## ADDED Requirements

### Requirement: 后台子 Agent 执行模型

SuperAgent SHALL 通过进程内 `BackgroundSubagentExecutor` 执行委派子任务：任务在专用守护线程的独立事件循环上运行，生命周期归属 session 而非主 run。任务状态机 SHALL 覆盖 running / awaiting_approval / completed / failed / cancelled / timed_out；每会话并发上限与单任务超时 SHALL 由 `subagents` 配置约束。进程重启后运行中任务 SHALL 丢失（注册表在内存，接受的设计限制），`check_task` 对不存在的 task_id SHALL 返回可诊断提示。

#### Scenario: 委派后主 Agent 继续

- **WHEN** 主 Agent 以默认参数调用 `start_task(description)`
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

### Requirement: 单工具同异步参数（run_in_background）

委派 SHALL 只有一个工具入口 `start_task`，其 `run_in_background` 参数（默认 true）由模型按依赖关系选择：true 立即返回 task_id（后台）；false 为**前台等待**——执行仍走同一后台路径（隔离 loop / 注册表 / 超时），工具 await 终态并把终态文本作为工具返回值。系统 SHALL NOT 提供第二条同步委派执行路径。

#### Scenario: 前台等待返回结果

- **WHEN** 模型调用 `start_task(description, run_in_background=false)`
- **AND** 子任务到达 completed
- **THEN** 工具 SHALL 返回子任务的最终小结文本

#### Scenario: 前台等待期间审批

- **WHEN** 前台任务中途触发工具审批
- **THEN** 工具 SHALL 持续等待至审批后续跑并到达终态；审批触达走用户面板

#### Scenario: 前台等待不阻塞事件循环

- **WHEN** 工具在前台等待
- **THEN** 主 run 事件循环 SHALL 保持可响应（等待经跨 loop 桥接实现）

### Requirement: task-worker 编译契约

后台 task-worker SHALL 经 async 工厂在隔离事件循环内惰性编译：LLM 客户端与 checkpointer 连接池 SHALL 绑定隔离 loop（`create_isolated_checkpointer` 独立建池，共用 checkpoint 库）。task-worker 带 checkpointer（thread_id = task_id）与 SUBAGENT profile 中间件栈，且 SHALL NOT 携带后台任务工具自身（禁止递归委派）。task-worker 与主 Agent SHALL 共享同一沙箱 backend。

#### Scenario: 递归委派防护

- **WHEN** task-worker 的工具集被组装
- **THEN** 其中 SHALL NOT 出现 start_task / check_task / cancel_task / list_tasks / send_message

#### Scenario: 隔离资源绑定

- **WHEN** 后台任务首次执行
- **THEN** 其 LLM 客户端与 checkpointer 连接池 SHALL 在隔离 loop 内创建，不复用主 loop 实例

### Requirement: Followup-turn 续话

`send_message(task_id, message)` SHALL 向子任务的 checkpointer thread 追加一个 turn，系统 SHALL NOT 以中途注入方式改写当前 turn 的模型输入：

- 任务 running：消息入 FIFO followup 队列（上限 10）；当前 turn 结束后 executor SHALL 同 thread 追加 `HumanMessage` 链式开新 turn，队列清空前任务保持 running。
- 任务 awaiting_approval：消息入队，审批 resume 完成本 turn 后由同一条链消费。
- 任务 completed：SHALL 冷恢复——同 thread 追加消息开新 turn，任务回到 running，结束后更新结果。
- 任务 failed / timed_out / cancelled：SHALL 返回错误说明，不可续。

指令 SHALL 经模型工具 `send_message` 与用户 API `POST /bg-tasks/{id}/message` 两个入口投递（人 / 模型同路径）。注入式 SteeringMiddleware SHALL 退役。

#### Scenario: 运行中追加指示

- **WHEN** 任务 running 时投递「聚焦中文源」
- **THEN** 当前 turn 结束后子 Agent SHALL 以该消息为新 turn 接续推理（可多轮工具调用）
- **AND** 新 turn 结束前消息 SHALL NOT 消失或重复

#### Scenario: 完成后继续追问

- **WHEN** 向 completed 任务 send_message 追问
- **THEN** 任务 SHALL 回到 running 并在同 thread 开新 turn，结束后结果 SHALL 更新

#### Scenario: 失败任务拒绝续话

- **WHEN** 向 failed / timed_out / cancelled 任务 send_message
- **THEN** SHALL 返回「任务已结束（原因）」类错误说明

### Requirement: 子会话查看

后台任务的子 Agent 完整消息历史 SHALL 持久于隔离 checkpointer 的 `thread_id = task_id`。`GET /bg-tasks/{task_id}/messages` SHALL 读取该 thread 状态并返回轻量视图项（角色 / 文本或工具调用名与参数摘要 / 工具结果状态与预览），读取 SHALL 为只读操作（`aget_state`，不改写状态机），归属校验与其他后台任务 API 一致。前端 SHALL 在任务卡提供「查看详情」入口，渲染完整子会话（模型轮次、工具调用、结果、审批暂停点）；步骤摘要轮询 SHALL 保留为收起态概览。

#### Scenario: 查看运行中任务过程

- **WHEN** 任务 running 时请求其 messages
- **THEN** SHALL 返回截至当前 turn 的已提交消息视图项，不干扰执行

#### Scenario: 查看已完成任务

- **WHEN** 任务 completed 后请求其 messages
- **THEN** SHALL 返回完整子会话历史，含最终小结 turn

#### Scenario: 越权访问

- **WHEN** 用户 A 读取用户 B 的后台任务会话
- **THEN** SHALL 返回 404 语义

#### Scenario: 进程重启后查看历史任务

- **WHEN** 进程重启后请求重启前已完成任务的 messages
- **THEN** SHALL 经持久层快照校验归属后，从 checkpoint 库返回该 thread 的完整子会话历史
- **AND** 持久层也无快照的 task_id SHALL 返回 404 语义

### Requirement: 完成通知注入

后台任务到达终态时 SHALL 向所属会话的待送达通知队列写入一条通知（task_id、终态、结果预览 ≤80 字）。该会话下一次 run 启动组装输入前 SHALL drain 队列并以 `[系统通知]` 前缀注入本轮上下文，注入一次性且 SHALL NOT 写入消息落库内容。系统 SHALL NOT 主动为通知启动 run；前端轮询为用户侧兜底触达。`awaiting_approval` SHALL NOT 注入模型通知。

#### Scenario: 下一轮收到通知

- **WHEN** 后台任务完成，用户随后发送新消息
- **THEN** 本轮模型输入 SHALL 以 `[系统通知]` 前缀包含该任务完成提示与 check_task 指引
- **AND** 再下一轮 SHALL NOT 重复出现该通知

#### Scenario: 用户消息原文不受污染

- **WHEN** 通知被注入某轮上下文
- **THEN** 该轮用户消息的数据库持久化内容 SHALL 与用户原始输入一致

#### Scenario: 不主动唤醒

- **WHEN** 后台任务完成但用户未再发消息
- **THEN** 系统 SHALL NOT 自行启动模型调用；前端任务面板 SHALL 经轮询显示终态
