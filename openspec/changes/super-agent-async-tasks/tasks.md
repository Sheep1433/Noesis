# Tasks: SuperAgent 全异步后台子 Agent

> 基线（executor / HITL 审批 / API / BgTaskPanel / 过程捕获）已完成；§3–§5 为初版闭环（已实现，其中 steering 将被 §7 修订取代）；§7 为设计评审修订后的最终形态。

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
- [x] 4.5 BgTaskPanel 任务卡发送指令输入（抽屉详情底部 composer，运行中/已完成 continuable 任务可用）

## 5. 完成通知

- [x] 5.1 executor 终态回调写会话级待送达通知队列（进程内；含 task_id、终态、结果预览 ≤80 字）
- [x] 5.2 `exec_query` 组装本轮输入前 drain 通知，拼 `[系统通知]` 前缀（不落库、只注入一次）；多任务终态合并为一条通知
- [x] 5.3 单测：通知一次性消费、不污染用户消息原文（DB 内容断言）、无通知时零开销

## 6. 收口

- [x] 6.1 后端全量回归 + 前端 lint/build；确认无新增失败（基线 11 个既有失败除外）
- [x] 6.2 手动验收脚本：acceptance.md（8 节：委派/子会话/followup/通知/审批/前台等待/后台命令/回归基线）
- [ ] 6.3 归档准备：spec delta 并入 `agent-background-tasks` / `agent-hitl` / `agent-profiles` 主规格

## 7. 设计修订：单工具同异步 / followup-turn / 子会话查看

- [x] 7.1 `start_task` 增加 `run_in_background` 参数（默认 true）；false 走 `asyncio.wrap_future` 跨 loop 前台等待终态并返回文本；契约测试（前台返回结果 / 审批期间持续等待 / 不阻塞事件循环）
- [x] 7.2 followup 队列（FIFO 上限 10）替代 steering 队列：running/awaiting_approval 入队，当前 turn 结束后链式 `ainvoke(HumanMessage)` 同 thread 开新 turn；completed 冷恢复回 running；failed/timed_out/cancelled 拒续；契约测试
- [x] 7.3 删除 SteeringMiddleware 与 steering.py（含 worker 装配与相关测试）；`POST /bg-tasks/{id}/message` 语义同步为 followup
- [x] 7.4 子会话读取：`GET /bg-tasks/{id}/messages`（只读 aget_state → 轻量视图项 + 归属校验）；契约测试
- [x] 7.5 前端：任务卡「查看详情」子会话抽屉（完整消息历史渲染）；`send_message` 用户侧入口复用
- [x] 7.6 prompt 更新：依赖结果选 `run_in_background=false`；send_message 语义改为追加 turn；更新关键词断言
- [x] 7.7 全量回归 + 手动验收脚本更新（acceptance.md 覆盖前台等待 / followup / 子会话 / 后台命令路径；后端 1045 passed、前端 lint/test/build 绿）

## 8. 后台命令任务（execute run_in_background）

- [x] 8.1 executor 泛化：`start_shell`（kind="shell"，不经 worker 编译，backend.aexecute 执行；无 awaiting_approval；`shell_task_timeout_seconds` 默认 0=不限时）；会话沙箱销毁时运行中任务转 failed（`fail_session_shell_tasks`，挂接 `destroy_session_sandbox`）
- [x] 8.2 工具替换：`shell_tool.replace_execute_tool`（经 `filesystem_middleware_hook` 在 stack 装配层替换；同名 + `run_in_background` 默认 false；false 原样委托原工具，true 走 start_shell 立即返回 task_id）；同名断言覆盖 interrupt_on["execute"] 按名匹配；真实 FilesystemMiddleware 集成验证前台输出不变
- [x] 8.3 check_task 对 shell 任务返回 exit code + 有界 stdout/stderr 尾部（`_format_shell_result`，尾部 4000 字符）；shell 任务 one_shot 语义（send_message 拒绝）；cancel_task 复用
- [x] 8.4 前端 BgTaskPanel：shell 任务卡带「命令」标记 + 子会话视图由命令/结果合成（后端 shell 分支），无 followup 输入；终态通知走既有 bg_task_notice 管线（`_notify_terminal` 通用）
- [x] 8.5 prompt：长命令后台化指引（预期超过几十秒设 run_in_background=true）+ 关键词断言
- [x] 8.6 契约测试：tests/test_bg_shell_tasks.py（9 例：生命周期/输出截断/one_shot/沙箱销毁/超时/前台委托零变化/后台启动/超并发优雅拒绝/无 execute 工具静默跳过）
