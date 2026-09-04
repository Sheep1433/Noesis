# agent-background-tasks Delta

## MODIFIED Requirements

### Requirement: 后台子 Agent 执行模型

SuperAgent SHALL 通过进程内 `BackgroundSubagentExecutor` 执行委派子任务：任务在专用守护线程的独立事件循环上运行，生命周期归属 session 而非主 run。任务状态机 SHALL 覆盖 running / stopping / awaiting_approval / completed / failed / cancelled / timed_out（`stopping` 为停止已受理、执行收尾中的中间态，仍占并发槽）；每会话并发上限与单任务超时 SHALL 由 `subagents` 配置约束（后台命令任务超时独立约束，停止宽限期 `stop_grace_seconds` 同组配置）。注册表在内存：进程重启 SHALL 丢失运行中任务与 shell job（接受的设计限制）；重启后遗留的活跃 child run SHALL 由启动对账收口为 error（`SUBAGENT_PROCESS_RESTARTED`）；`check_task` 对不存在的 task_id SHALL 返回可诊断提示。

#### Scenario: 委派后主 Agent 继续

- **WHEN** 主 Agent 以默认参数调用 `start_task(description)`
- **THEN** 工具 SHALL 立即返回 child session id 与「可继续其他工作」提示
- **AND** 主 Agent 本轮 SHALL 不等待子任务完成

#### Scenario: 跨轮收取结果

- **WHEN** 主 Agent 所在 run 已结束，后台任务随后完成
- **THEN** 任务 SHALL 保持在注册表中，任意后续轮次 `check_task` SHALL 返回终态与结果

#### Scenario: 并发上限

- **WHEN** 某会话 running + stopping + awaiting_approval 任务数已达 `max_concurrent_per_session`
- **THEN** `start_task` SHALL 返回错误说明，已创建的 child session SHALL 被清理
- **AND** 其他会话不受影响

#### Scenario: 进程重启

- **WHEN** 后端进程重启
- **THEN** 重启前活跃的 child run SHALL 被启动对账标记为 error，assistant 消息同步置 error
- **AND** `check_task` 对重启前任务 SHALL 返回「任务不存在」类提示，不抛异常

## ADDED Requirements

### Requirement: 协作式停止与部分成果回收

停止一个后台子 Agent SHALL 是「同步信号 + 协作退出 + 成果回收」：`cancel`（用户 stop API、主 Agent `cancel_task` 工具或超时 watchdog）SHALL 同步把任务置为 `stopping` 并立即返回该快照，SHALL NOT 直接取消执行协程；对已处于 `stopping` 的任务再次调用 SHALL 幂等返回同一快照。执行循环 SHALL 只在**静止边界**（最新消息为工具结果、或无工具调用的 AI 消息）检查停止请求——带未应答 tool_calls 的快照点 SHALL 先让工具节点执行完毕再退出，保证线程不残留悬空工具调用；退出后终态为 `cancelled`（超时触发的为 `timed_out`）。任务因 cancelled / timed_out 终止时，系统 SHALL 从子会话已落库投影中提取全部文本产出作为部分成果，以「中止前部分产出」标注写入 `task.result`，并使 `check_task` 返回与父 Agent 通知注入一致携带（终态通知 preview 从提取内容开头截取，标注前缀不占预览字符预算）。`stopping` 期间当前步骤触发 HITL interrupt 时停止请求 SHALL 优先：任务直接按取消收尾，SHALL NOT 进入 awaiting_approval。`stopping` 超过 `stop_grace_seconds`（默认 30s）SHALL 回退为硬杀收尾（终态与成果回收语义不变）；排队与 awaiting_approval 任务的停止 SHALL 即时终态（无执行面，无 `stopping`）。

#### Scenario: 停止请求即时受理

- **WHEN** 用户对 running 子任务调用 `POST /api/chat/runs/{run_id}/stop`
- **THEN** 响应 SHALL 为 DB run 快照形状且 status 覆写为 stopping（不等待终态）
- **AND** `bg-task` SSE 事件 SHALL 同步推送 stopping 快照，前端任务卡 SHALL 显示「停止中」

#### Scenario: 重复停止幂等

- **WHEN** 对已处于 stopping 的任务再次调用 stop 或 `cancel_task`
- **THEN** SHALL 返回同一 stopping 快照，不产生重复状态事件或副作用

#### Scenario: 当前步骤完整结束且不残留悬空工具调用

- **WHEN** 停止请求到达时子 Agent 正在执行某一步骤，或刚产出一条带 tool_calls 的 AI 消息
- **THEN** 该步骤（含其工具调用与结果）SHALL 完整结束：产出进入子会话投影并落库，thread 停在无未应答 tool_calls 的快照点
- **AND** 任务 SHALL 随后终态为 cancelled，子会话历史保持可 followup 续跑

#### Scenario: stopping 期间触发 HITL

- **WHEN** 已请求停止的任务在收尾期间当前步骤触发审批中断
- **THEN** 任务 SHALL 直接按取消收尾，SHALL NOT 进入 awaiting_approval 等待审批

#### Scenario: 部分成果回收

- **WHEN** 子任务已产出若干文本后到达 cancelled / timed_out 终态
- **THEN** `task.result` SHALL 以「中止前部分产出」标注携带子会话投影中的全部文本产出（有界截断）
- **AND** `check_task` 返回与父 Agent 通知注入 SHALL 携带同一内容，通知 preview SHALL 从提取内容开头截取（标注前缀不占预览预算）
- **AND** 主 Agent 据此可将被停任务的部分成果并入交付

#### Scenario: 停止宽限兜底

- **WHEN** 任务处于 stopping 超过 `stop_grace_seconds` 仍未退出（如极长的单步骤工具执行）
- **THEN** 系统 SHALL 硬杀执行协程完成终止，终态与部分成果回收语义与协作路径一致（回收终止前最后一个完整步骤的产出）
- **AND** 硬杀路径 SHALL 走完整终态收尾（事件发布、通知、落库、排队唤醒），SHALL NOT 漏发终态通知

#### Scenario: 无执行面的停止即时完成

- **WHEN** 对 queued 或 awaiting_approval 任务请求停止
- **THEN** 任务 SHALL 即时终态为 cancelled，不经过 stopping

#### Scenario: 工具与状态查询语义

- **WHEN** 主 Agent 对 running 任务调用 `cancel_task`
- **THEN** 工具 SHALL 返回「已请求停止（当前步骤完成后停止，可用 check_task 收取部分产出）」
- **WHEN** 主 Agent 对 stopping 任务调用 `check_task`
- **THEN** SHALL 返回「正在停止（当前步骤完成后退出）」

### Requirement: 输出截断的一等终止语义

子 Agent run 内模型响应以 `finish_reason=length` 截断（含工具参数被拦腰截断的调用）时，系统 SHALL 把该次截断记为一等终止事实：截断标记的作用域为单 run（turn）——同轮内 sticky（后续正常完成的步骤 SHALL NOT 降级该轮的截断终态），跨轮 SHALL NOT 传染（followup 新轮不带旧轮标记，任务终态由最后一轮决定）。带截断标记的轮次终态 SHALL 为 `partial`（finish_reason=`truncated`）而非 completed，并 SHALL 按部分成果回收语义携带「中止前部分产出」。该语义与模型边界的截断告警（可观测性）互补，SHALL NOT 影响主聊天 run 的终态判定。

#### Scenario: 截断终态

- **WHEN** 子 Agent 的模型响应以 finish_reason=length 截断且该轮未能自愈完成
- **THEN** 该轮 run 终态 SHALL 为 partial / truncated，assistant 消息与通知 SHALL 反映截断原因并携带部分产出

#### Scenario: 单轮 sticky 不降级

- **WHEN** 同一轮内早前步骤发生截断、后续步骤正常完成
- **THEN** 该轮终态 SHALL 保持 partial / truncated，SHALL NOT 因后续步骤完成而改判 completed

#### Scenario: 跨轮不传染

- **WHEN** 早前轮次发生截断，随后的 followup 轮正常完成
- **THEN** 任务终态 SHALL 由最后一轮决定为 completed，早前轮的截断事实保留在对应 child run 历史中
