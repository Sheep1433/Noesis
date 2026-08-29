## 1. executor 协作停止状态机

- [ ] 1.1 `BgTaskStatus` 增加 `STOPPING`；`_SLOT_STATUSES` 包含 stopping（收尾仍占槽）；`BackgroundTask` 增加停止请求标记与宽限 watchdog 句柄字段
- [ ] 1.2 `cancel()` 重构：running 任务置 `STOPPING` 并发 `bg-task` 快照事件后立即返回（不再 `future.cancel`）；对已 stopping 任务幂等返回同一快照；queued / awaiting_approval 保持即时终态；`stop_grace_seconds` 进入 `subagents` 配置组（env.py + config.yaml，默认 30s）
- [ ] 1.3 `_arun` 的 astream 循环在每个 chunk 迭代处检查 stopping，但**只在静止边界退出**（最新消息为 ToolMessage 或无工具调用的 AI 消息；带未应答 tool_calls 的快照先让工具节点跑完），退出后走统一终态收尾（最后一次 `_persist_child_projection` 落库、`CANCELLED`、`mark_terminal`、通知、drain 既有路径）；stopping 期间触发 HITL interrupt 时直接按取消收尾，不进入 awaiting_approval
- [ ] 1.4 停止宽限 watchdog：进入 stopping 时装载，超时回退硬杀（`future.cancel()`）；`_arun` 的 `CancelledError` 分支升级为完整终态收尾（事件发布 / 通知 / `mark_terminal` / drain 统一下沉到 `_arun` 终态路径，`cancel()` 只负责置位与快照返回）；超时 watchdog（任务总时限）改走同一协作路径（置 stopping + 终止原因 timed_out）
- [ ] 1.5 回归测试：停止请求即时返回 stopping；重复停止幂等；静止边界退出（工具调用有结果应答、无悬空 tool_calls、投影含最后一步产出、followup 可续跑）；stopping 期间 HITL 直接取消；宽限超时硬杀兜底且终态通知不漏发；queued / awaiting_approval 即时取消；并发上限计入 stopping

## 2. 部分成果回收

- [ ] 2.1 终态收尾协程（主 loop）从子会话 assistant 消息投影提取全部 `type=text` parts，以「中止前部分产出」标注写入 `task.result`（有界截断），先提取后记录通知；提取失败降级为空 preview 不阻塞终止
- [ ] 2.2 `tools.py`：`check_task` 对 cancelled / timed_out 返回终止原因 + 部分产出（受 `tool_output_max_chars` 预算约束）；对 stopping 返回「正在停止」；`cancel_task` 返回改为「已请求停止（当前步骤完成后停止，可用 check_task 收取部分产出）」
- [ ] 2.3 通知链路验证：取消终态通知 preview 从提取内容开头截取（「中止前部分产出」前缀只出现在 `task.result` 与 `check_task` 全文，不占 ≤80 字预览预算），父 Agent 通知注入文本携带同一内容
- [ ] 2.4 回归测试：有产出的任务取消后通知 / check_task / task.result 三处一致携带部分内容；无产出任务取消不产生空占位文本

## 3. 输出截断一等终止

- [ ] 3.1 `_arun` 消费 chunk 时检测**新增** AIMessage 的 `response_metadata.finish_reason == "length"`，置本轮截断标记（作用域单 run/turn，followup 新轮重置）
- [ ] 3.2 终态合成：带截断标记的轮次终态为 `partial`（finish_reason=`truncated`），走部分成果回收路径；同轮后续正常步骤不降级；任务终态由最后一轮决定
- [ ] 3.3 回归测试：截断轮终态 partial/truncated 且携带部分产出；单轮内 sticky 不降级；早轮截断后 followup 轮正常完成时任务终态 completed；主聊天 run 终态不受影响

## 4. stop API 与服务层

- [ ] 4.1 `SubagentSessionService.stop_run` 不再 `_wait_run` 等待终态：调用 cancel 后返回 **DB run 快照、status 字段覆写为 `stopping`**（响应形状与现有 RunSnapshot 一致）；`POST /api/chat/runs/{run_id}/stop` 路径不变，非破坏性
- [ ] 4.2 API 契约测试更新：stop 子 Agent run 返回 stopping 覆写快照；终态经 `bg-task` / run `run.finished` SSE 事件推送（不新增事件类型）
- [ ] 4.3 高风险区检查：取消路径与消息持久化交界——`mark_terminal(PARTIAL)` 收口、assistant 消息状态、`_drain_session_queue` 唤醒顺序在协作退出与硬杀兜底两条路径下均保持既有语义

## 5. 前端适配

- [ ] 5.1 任务状态联合类型与文案：`TaskCatalogPanel` / `BackgroundSubagentCollapse` / 共享状态文案（`utils/taskStatusLabels.ts`）识别 `stopping`（「停止中」+ 灰色状态点）
- [ ] 5.2 子会话抽屉（`SubagentConversationView`）：`runActive` 涵盖 `stopping`——停止按钮保持停止形态（或禁用），输入框发送维持「运行中排队」语义，SHALL NOT 切换为直发（避免消息被送进正在取消任务的 followup 队列）；`runStatusLabel` 增加 stopping 文案
- [ ] 5.3 任务卡与目录抽屉在 stopping 期间禁用重复停止操作；终态到达后按既有 bg-task 事件更新
- [ ] 5.4 前端回归：停止中状态展示（任务卡 + 抽屉）、stopping 期间发送走排队、终态后部分产出在 `check_task` 结果与通知条中的呈现；vitest 全量通过
