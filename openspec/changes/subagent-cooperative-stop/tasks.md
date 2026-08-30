# Tasks: subagent-cooperative-stop（统一管道内核版）

> 原 tasks.md 按旧 `astream(values)` 内核编写；unify-agent-run-pipeline 合入后 executor 执行内核
> 已是统一 run 管道（`stream_agent_events` → `RuntimeEventMapper` → typed RunEvent，单 turn 入口
> `_run_turn_via_pipeline`）。本任务面按新内核重写：静止边界 = mapper 事件流的模型/工具消息边界，
> 协作退出信号在 `_run_turn_via_pipeline` 事件循环内检查，不再依赖 `future.cancel` 硬杀（仅宽限超时兜底）。

## 1. executor 协作停止状态机

- [ ] 1.1 `BgTaskStatus` 增加 `STOPPING`；`_SLOT_STATUSES` 包含 stopping（收尾仍占槽）；`BackgroundTask` 增加停止请求标记（`stop_requested`，含终止原因 `timed_out`/`cancelled`）与宽限 watchdog 句柄字段；`to_dict` 暴露 stopping 状态
- [ ] 1.2 `cancel()` 重构：running 任务置 `STOPPING` + 置停止请求标记并发 `bg-task` 快照事件后立即返回（不再 `future.cancel`）；对已 stopping 任务幂等返回同一快照；queued / awaiting_approval 保持即时终态；`stop_grace_seconds` 进入 `subagents` 配置组（env.py + config.yaml，默认 30s）
- [ ] 1.3 `_run_turn_via_pipeline` 的 raw 事件循环检查停止请求，但**只在静止边界协作退出**：
  - 静止边界 = `on_tool_end`（工具结果已落定并投影）或 `on_chat_model_end` 输出无未应答 tool_calls（本轮模型消息完整）；进行中的工具/模型调用让其跑完
  - 退出时置 `_TurnOutcome.cooperative_stop=True`，`_arun` 按取消收尾（终态 `CANCELLED`、`mark_terminal(PARTIAL, finish_reason=cancelled)`、`_notify_terminal`、drain 既有路径），最后一次投影已含边界前全部产出
  - stopping 期间收到 `HitlRequired` 事件 → 直接按取消收尾，不进入 awaiting_approval
- [ ] 1.4 停止宽限 watchdog：进入 stopping 时装载，超时回退硬杀（`future.cancel()`）；`_arun` 的 `CancelledError` 分支升级为完整终态收尾（事件发布 / 通知 / `mark_terminal` / drain 统一下沉到 `_arun` 终态路径，`cancel()` 只负责置位与快照返回）；超时 watchdog（任务总时限）改走同一协作路径（置 stopping + 终止原因 timed_out，宽限内仍走静止边界）
- [ ] 1.5 回归测试（真实 create_agent 图，沿用既有 `_build_worker` 桩）：停止请求即时返回 stopping；重复停止幂等；静止边界退出（工具调用有结果应答、投影含最后一步产出、followup 可续跑冷恢复）；stopping 期间 HITL 直接取消；宽限超时硬杀兜底且终态通知不漏发；queued / awaiting_approval 即时取消；并发上限计入 stopping

## 2. 部分成果回收

- [ ] 2.1 终态收尾（`_arun` 取消分支）从 `_TurnOutcome.content`（统一管道 builder 产物）提取全部 `type=text` parts 拼接，以「中止前部分产出」标注写入 `task.result`（受 `_SHELL_RESULT_TAIL_CHARS` 同量级截断），先提取后记录通知；无 text parts 不写占位
- [ ] 2.2 `tools.py`：`check_task` 对 cancelled / timed_out 返回终止原因 + 部分产出（受 `tool_output_max_chars` 预算约束）；对 stopping 返回「正在停止」；`cancel_task` 返回改为「已请求停止（当前步骤完成后停止，可用 check_task 收取部分产出）」
- [ ] 2.3 通知链路验证：取消终态通知 preview 从提取内容开头截取（「中止前部分产出」前缀只出现在 `task.result` 与 `check_task` 全文，不占 ≤80 字预览预算），父 Agent 通知注入文本携带同一内容
- [ ] 2.4 回归测试：有产出的任务取消后通知 / check_task / task.result 三处一致携带部分内容；无产出任务取消不产生空占位文本

## 3. 输出截断一等终止

- [ ] 3.1 `_run_turn_via_pipeline` 在 `on_chat_model_end` 边界检测**新增** AIMessage 的 `response_metadata.finish_reason == "length"`，置本轮截断标记（`_TurnOutcome.truncated=True`，作用域单 turn，followup 新轮天然重置——builder 逐 turn 独立）
- [ ] 3.2 终态合成：带截断标记的轮次终态为 `partial`（finish_reason=`truncated`），走部分成果回收路径；任务终态由最后一轮决定（早轮截断后 followup 轮正常完成 → 任务 completed）
- [ ] 3.3 回归测试：截断轮终态 partial/truncated 且携带部分产出；早轮截断后 followup 轮正常完成时任务终态 completed；主聊天 run 终态不受影响（executor 侧标记，不触碰主链路 bridge）

## 4. stop API 与服务层

- [ ] 4.1 `SubagentSessionService.stop_run` 不再 `_wait_run` 等待终态：调用 cancel 后返回 **DB run 快照、status 字段覆写为 `stopping`**（响应形状与现有 RunSnapshot 一致）；`POST /api/chat/runs/{run_id}/stop` 路径不变，非破坏性
- [ ] 4.2 API 契约测试更新：stop 子 Agent run 返回 stopping 覆写快照；终态经 `bg-task` / run `run.finished` SSE 事件推送（不新增事件类型）
- [ ] 4.3 高风险区检查：取消路径与消息持久化交界——`mark_terminal(PARTIAL)` 收口、assistant 消息状态、`_drain_session_queue` 唤醒顺序在协作退出与硬杀兜底两条路径下均保持既有语义

## 5. 前端适配

- [ ] 5.1 任务状态联合类型与文案：`TaskCatalogPanel` / `BackgroundSubagentCollapse` / 共享状态文案（`utils/taskStatusLabels.ts`）识别 `stopping`（「停止中」+ 灰色状态点）
- [ ] 5.2 子会话抽屉（`SubagentConversationView`）：`runActive` 涵盖 `stopping`——停止按钮保持停止形态，输入框发送维持「运行中排队」语义，SHALL NOT 切换为直发（避免消息被送进正在取消任务的 followup 队列）；`runStatusLabel` 增加 stopping 文案
- [ ] 5.3 任务卡与目录抽屉在 stopping 期间禁用重复停止操作；终态到达后按既有 bg-task 事件更新
- [ ] 5.4 前端回归：停止中状态展示（任务卡 + 抽屉）、stopping 期间发送走排队、终态后部分产出在 `check_task` 结果与通知条中的呈现；vitest 全量通过

## 6. 顺带收口（unify review 遗留）

- [ ] 6.1 HITL 挂起段 usage 补齐：`mark_waiting_approval` 时把 bridge 累计的 `message_usage` 一并落 run 快照，审批 resume 的新 turn 完成后与后半段 usage 合并落库（该 turn `extra.usage` 含中断前后全部调用）——中断/恢复路径与停止路径同属 turn 生命周期，随本变更一次收口
- [ ] 6.2 回归测试：含审批的子 turn 终态 usage ≥ 中断前模型调用的 usage（不再丢前半段）
