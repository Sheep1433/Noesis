## ADDED Requirements

### Requirement: 子 Agent 会话身份

每次 `start_task` SHALL 在父会话下创建一个独立的 child `ChatSession`（`kind=subagent`、`parent_id` 指向父会话、`created_by_tool_call_id` 记录触发调用的模型 tool_call_id），并在同一 launch 事务中写入首条 user message、assistant 骨架与首个标准 `TAgentRun`。executor 内部 task id 与 child session id SHALL 分离；对外 API 与前端 SHALL 只以 child session id 作为公开身份（目录 `task_id == child_session_id`，`bg-*` 内部 id 不外泄）。左侧会话历史 SHALL 过滤子会话（`parent_id IS NULL`）。父会话软删 SHALL 级联软删 child session 树及其消息，并取消运行中的 run 与后台任务。

#### Scenario: 并行同名独立

- **WHEN** 主 Agent 并行调用两次 `start_task` 且 description 相同
- **THEN** SHALL 创建两个独立 child session / run，`created_by_tool_call_id` 一一对应
- **AND** 父会话卡片 SHALL 各自独立，不按名称合并

#### Scenario: 左侧历史不出现子会话

- **WHEN** 子会话存在且未删除
- **THEN** 会话历史列表 SHALL 不返回该子会话

#### Scenario: 父会话软删级联

- **WHEN** 用户删除父会话
- **THEN** 子会话树与消息 SHALL 级联软删，运行中 run 与后台任务 SHALL 被取消

### Requirement: 后台子 Agent 执行模型

SuperAgent SHALL 通过进程内 `BackgroundSubagentExecutor` 执行委派子任务：任务在专用守护线程的独立事件循环上运行，生命周期归属 session 而非主 run。任务状态机 SHALL 覆盖 running / awaiting_approval / completed / failed / cancelled / timed_out；每会话并发上限与单任务超时 SHALL 由 `subagents` 配置约束（后台命令任务超时独立约束）。注册表在内存：进程重启 SHALL 丢失运行中任务与 shell job（接受的设计限制）；重启后遗留的活跃 child run SHALL 由启动对账收口为 error（`SUBAGENT_PROCESS_RESTARTED`）；`check_task` 对不存在的 task_id SHALL 返回可诊断提示。

#### Scenario: 委派后主 Agent 继续

- **WHEN** 主 Agent 以默认参数调用 `start_task(description)`
- **THEN** 工具 SHALL 立即返回 child session id 与「可继续其他工作」提示
- **AND** 主 Agent 本轮 SHALL 不等待子任务完成

#### Scenario: 跨轮收取结果

- **WHEN** 主 Agent 所在 run 已结束，后台任务随后完成
- **THEN** 任务 SHALL 保持在注册表中，任意后续轮次 `check_task` SHALL 返回终态与结果

#### Scenario: 并发上限

- **WHEN** 某会话 running + awaiting_approval 任务数已达 `max_concurrent_per_session`
- **THEN** `start_task` SHALL 返回错误说明，已创建的 child session SHALL 被清理
- **AND** 其他会话不受影响

#### Scenario: 进程重启

- **WHEN** 后端进程重启
- **THEN** 重启前活跃的 child run SHALL 被启动对账标记为 error，assistant 消息同步置 error
- **AND** `check_task` 对重启前任务 SHALL 返回「任务不存在」类提示，不抛异常

### Requirement: 单工具同异步参数（run_in_background）

委派 SHALL 只有一个工具入口 `start_task`，其 `run_in_background` 参数（默认 true）由模型按依赖关系选择：true 立即返回（后台）；false 为**前台等待**——执行仍走同一后台路径（隔离 loop / 注册表 / 超时），工具 await 终态并把终态文本作为工具返回值。前台等待超过 `foreground_max_wait_seconds`（默认 120s）SHALL 自动转为后台并提示稍后 `check_task` 收果（等待经 shield 实现，取消不波及底层任务）。系统 SHALL NOT 提供第二条同步委派执行路径。

#### Scenario: 前台等待返回结果

- **WHEN** 模型调用 `start_task(description, run_in_background=false)` 且子任务到达 completed
- **THEN** 工具 SHALL 返回子任务的最终小结文本（含 child session id 供前端关联）

#### Scenario: 前台等待超时转后台

- **WHEN** 前台等待超过 `foreground_max_wait_seconds`
- **THEN** 工具 SHALL 返回「已自动转为后台」提示，任务 SHALL 继续后台执行不被取消

#### Scenario: 前台等待期间审批

- **WHEN** 前台任务中途触发工具审批
- **THEN** 工具 SHALL 持续等待至审批后续跑并到达终态；审批触达走用户面板

### Requirement: task-worker 编译契约

后台 task-worker SHALL 经 async 工厂在隔离事件循环内惰性编译：LLM 客户端与 checkpointer 连接池 SHALL 绑定隔离 loop（`create_isolated_checkpointer` 独立建池，共用 checkpoint 库）。task-worker 带 checkpointer 与 SUBAGENT profile 中间件栈，且 SHALL NOT 携带后台任务工具自身（禁止递归委派）。task-worker 与主 Agent SHALL 共享同一沙箱 backend。

#### Scenario: 递归委派防护

- **WHEN** task-worker 的工具集被组装
- **THEN** 其中 SHALL NOT 出现 start_task / check_task / cancel_task / list_tasks / send_message

#### Scenario: 隔离资源绑定

- **WHEN** 后台任务首次执行
- **THEN** 其 LLM 客户端与 checkpointer 连接池 SHALL 在隔离 loop 内创建，不复用主 loop 实例

### Requirement: Followup 续话（子会话追加 turn）

`send_message(task_id, message)`（模型侧）与 `POST /sessions/{id}/subagent-followup`（用户侧，人 / 模型同路径）SHALL 为同一 child session 追加一条 user message 与一个新的标准 `TAgentRun`，SHALL NOT 以中途注入方式改写当前 turn 的模型输入：

- 任务 running：消息排队（FIFO，上限 10）；当前 turn 结束后 executor SHALL 同 thread 链式开新 turn，队列清空前任务保持 running。
- 任务 awaiting_approval：消息入队，审批 resume 完成本 turn 后由同一条链消费。
- 任务 completed：SHALL 冷恢复——同 thread 追加消息开新 turn，任务回到 running，结束后更新结果。
- 任务 failed / timed_out / cancelled：SHALL 返回错误说明，不可续。

#### Scenario: 运行中追加指示

- **WHEN** 任务 running 时投递「聚焦中文源」
- **THEN** 当前 turn 结束后子 Agent SHALL 以该消息为新 turn 接续推理（可多轮工具调用）
- **AND** 新 turn 结束前消息 SHALL NOT 消失或重复

#### Scenario: 完成后继续追问

- **WHEN** 向 completed 任务 send_message 追问
- **THEN** 任务 SHALL 回到 running 并开新 turn，结束后结果 SHALL 更新

#### Scenario: 失败任务拒绝续话

- **WHEN** 向 failed / timed_out / cancelled 任务 send_message
- **THEN** SHALL 返回「任务已结束（原因）」类错误说明

### Requirement: 子会话详情与事件流

子会话正文 SHALL 读取标准会话消息（`GET /sessions/{id}/messages`，与其他会话同一协议）；实时过程 SHALL 经 run 事件流订阅（`GET /runs/{run_id}/stream`）。checkpointer SHALL 只用于 LangGraph 执行恢复，SHALL NOT 作为产品消息读模型。事件流契约：

- 连接 SHALL 先订阅、再取权威快照、从快照序号重放历史（不重不漏，按 (type, sequence) 去重）；
- live 队列中低于客户端游标的事件 SHALL 跳过（run.started / run.finished / approval 事件不受过滤）；
- `run.finished` SHALL 终止流并发送 `[DONE]`；
- 客户端断开（关闭详情抽屉）SHALL 立即退订（generator finally），终态 run 只发快照 + `[DONE]`、不建立订阅。

前端 SHALL 复用主 Agent 的消息渲染组件（Markdown / 工具块 / 审批卡 / 输入框）；父会话只展示带 child session 引用的轻量卡片，目录与卡片打开同一详情视图。

#### Scenario: 断线重连恢复

- **WHEN** 详情抽屉重开或刷新后按游标重连
- **THEN** SHALL 从权威快照序号之后重放，不重复、不丢消息

#### Scenario: 关闭详情退订

- **WHEN** 用户关闭详情抽屉
- **THEN** SSE 订阅 SHALL 立即释放，不产生泄漏

#### Scenario: 越权访问

- **WHEN** 用户 A 读取用户 B 的子会话或 run 事件流
- **THEN** SHALL 返回 404 语义

### Requirement: 完成通知注入

后台任务到达终态时 SHALL 向所属会话的待送达通知队列写入一条通知（task_id、终态、结果预览 ≤80 字）。该会话下一次 run 启动组装输入前 SHALL drain 队列并以 `[系统通知]` 前缀注入本轮上下文，注入一次性且 SHALL NOT 写入消息落库内容。系统 SHALL NOT 主动为通知启动 run；前端轮询为用户侧兜底触达。`awaiting_approval` SHALL NOT 注入模型通知。

#### Scenario: 下一轮收到通知

- **WHEN** 后台任务完成，用户随后发送新消息
- **THEN** 本轮模型输入 SHALL 以 `[系统通知]` 前缀包含该任务完成提示与 check_task 指引
- **AND** 再下一轮 SHALL NOT 重复出现该通知

#### Scenario: 用户消息原文不受污染

- **WHEN** 通知被注入某轮上下文
- **THEN** 该轮用户消息的数据库持久化内容 SHALL 与用户原始输入一致

#### Scenario: 续跑通知不伪装用户输入

- **WHEN** continuation run 因通知自动创建
- **THEN** 其 user 消息落库时 SHALL 携带来源标记（`extra.source_kind = bg_task_notice`）
- **AND** 前端 SHALL 将带该标记的消息渲染为系统通知条，SHALL NOT 渲染为用户消息气泡
- **AND** 续跑事件（`bg-continuation`）SHALL 携带通知全文与 child session 引用，前端据此实时插入同形态通知条

#### Scenario: 不主动唤醒

- **WHEN** 后台任务完成但用户未再发消息
- **THEN** 系统 SHALL NOT 自行启动模型调用；前端任务面板 SHALL 显示终态

### Requirement: 后台命令任务（execute run_in_background）

`execute` 工具 SHALL 保留单工具形态并增加 `run_in_background` 参数（默认 false，前台执行路径与现状零变化）。`run_in_background=true` 时命令 SHALL 作为 shell job 进入现有注册表、状态机、完成通知与前端任务面板管线——不经 worker 编译，直接经 agent backend 执行（local_shell 宿主机 / docker 容器内）。shell job 非对话、不持久化（进程重启即丢，接受的设计限制）。工具替换 SHALL 保留 `execute` 工具名（`interrupt_on` 审批按名匹配，危险命令审批仍发生在启动前）。文件系统工具与 backend 接口 SHALL NOT 受影响。

#### Scenario: 长命令后台执行

- **WHEN** 模型调用 `execute(command, run_in_background=true)`
- **THEN** 工具 SHALL 立即返回 task_id 与「可继续其他工作，稍后 check_task 收果」提示
- **AND** 命令 SHALL 在原 backend 执行环境（docker 模式为会话容器内）运行，与文件系统工具共享同一文件系统

#### Scenario: 前台行为不变

- **WHEN** 模型调用 `execute(command)` 或 `execute(command, run_in_background=false)`
- **THEN** 执行路径 SHALL 与参数引入前完全一致（同步等待、timeout 参数语义、输出截断）

#### Scenario: 收果与输出

- **WHEN** shell 任务到达 completed 并被 `check_task` 收取
- **THEN** SHALL 返回 exit code 与有界的 stdout/stderr 尾部摘要
- **AND** shell 任务 SHALL 为非对话任务：可查看、可 `cancel_task`，SHALL NOT 支持 `send_message` 续话

#### Scenario: 超时与生命周期

- **WHEN** shell 后台任务运行
- **THEN** 其 SHALL NOT 受 subagent 任务超时约束，超时由 `shell_task_timeout_seconds` 独立控制（默认不限时）
- **AND** 会话沙箱销毁时运行中 shell 任务 SHALL 转 failed（错误注明容器回收）

#### Scenario: 完成通知复用

- **WHEN** shell 任务到达终态
- **THEN** SHALL 走与 subagent 任务相同的完成通知管线（run 内注入 / 续跑通知条），前端任务面板 SHALL 显示同形态任务卡
