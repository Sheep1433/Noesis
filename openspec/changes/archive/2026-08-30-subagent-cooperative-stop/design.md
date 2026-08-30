## Context

`BackgroundSubagentExecutor`（`backend/packages/noesis-core/src/noesis/agents/subagents/executor.py`）当前取消路径：`cancel()` 在 `_TASKS_LOCK` 内直接置 `CANCELLED`、`future.cancel()` 硬杀隔离 loop 上的执行协程，`_arun` 的 `CancelledError` 分支做内存收尾；`mark_terminal(PARTIAL)` 与终态通知异步落库。三个已确认的缺陷：

1. 硬杀使进行中的步骤（模型流 / 工具执行）半途而废：该步产出不进子会话投影，thread checkpoint 停在半步之间，事后 followup 续跑的历史不干净。
2. 部分成果无回收通道：`task.result` 为空 → 终态通知 preview 为空、`check_task` 对 cancelled 只返回 `[id] cancelled`，主 Agent 拿不到子 Agent 中止前的任何产出（尽管投影已持久化到取消前一刻）。
3. 无中间态：从「点停止」到「真正停下」之间，任务状态、UI、`check_task` 均无法表达「正在停止」；`stop_run` 服务层靠轮询 DB（约 0.4s 窗口）等待终态，存在竞态。

## Goals / Non-Goals

**Goals:**

- 取消 = 同步发出停止信号 + 任务进入 `stopping` 中间态 + 执行循环在步骤边界协作退出。
- 当前步骤完整结束：产出进投影、checkpoint 停在完整 super-step 边界，子会话历史保持可续跑。
- 非正常终止（取消 / 超时 / 输出截断）统一回收「中止前的部分产出」，通知 / `check_task` / 父 Agent 收取三条链路一致携带。
- 输出截断（`finish_reason=length`）成为一等终止原因且 sticky。

**Non-Goals:**

- 不做「停止时让子 Agent 再跑一轮 LLM 总结」——部分产出本身已可回收，额外总结轮增加成本与延迟且引入新的失败面。
- 不改动主聊天 run 的取消路径与 `RunStatus` 枚举（`stopping` 只存在于 executor 任务状态机、bg-task 事件与 stop 响应的覆写值，不进 DB run 状态）。
- 不做跨进程停止协调（执行面本就在单进程内，重启对账已有既有语义）。
- 不改动后台命令任务（shell job）的取消路径——shell job 无对话产出与轮次概念，进程级终止语义保持现状。

## Decisions

### D1. 停止信号：同步置位 + 静止边界协作退出

`cancel()` 改为：持锁校验任务存在与可停状态 → 置 `task.status = STOPPING`（排队任务除外：出队即 `CANCELLED`，无执行面）→ 不再 `future.cancel()` → 锁外发 `bg-task` 快照事件（status=stopping）→ 返回 stopping 快照。对已处于 `stopping` 的任务再次调用 SHALL 幂等返回同一快照。

`_arun` 的 `agent.astream(..., stream_mode="values")` 循环在每个 chunk（super-step 快照）迭代处检查 `task.status == STOPPING`，但**只在静止边界退出**：最新消息为 ToolMessage（工具节点刚完成）或无工具调用的 AI 消息。若当前 chunk 是带 tool_calls 的 AI 消息，SHALL 先让工具节点执行完再退出——否则线程会留下未应答的 tool_calls（悬空工具调用），后续 followup 续跑时 LLM 请求会被拒绝或需要补丁修复。退出后走统一终态收尾：提取部分成果（D3）→ `CANCELLED` → 既有 `mark_terminal` / 通知 / drain 路径。

stopping 期间当前步骤触发 HITL interrupt 时，停止请求 SHALL 优先：任务直接按取消收尾，SHALL NOT 进入 awaiting_approval 等待一个不会再来的审批。

超时 watchdog 复用同一机制：到期置 `STOPPING` + 记录终止原因为 `timed_out`，由协作路径收尾。

### D2. `stopping` 有界兜底：停止宽限期后硬杀

协作停止的代价是「等当前步骤」。增加 `stop_grace_seconds` 配置（`subagents` 配置组，默认 30s）：进入 `stopping` 时同时装载停止宽限 watchdog；当前步骤在宽限期内未结束则回退为硬杀（`future.cancel()`，行为等同现状），终止原因仍为取消 / 超时。排队任务、awaiting_approval 任务的取消保持即时（无执行协程）。

硬杀兜底要求 `_arun` 的 `CancelledError` 处理分支升级为完整终态收尾（现状只改内存状态，事件发布 / 通知 / `mark_terminal` / drain 由 `cancel()` 承担；协作化后这些统一下沉到 `_arun` 的终态路径，`cancel()` 只负责置位与快照返回），否则宽限硬杀会漏通知与落库。

并发槽语义：`stopping` 仍占槽（协程尚在收尾），`_SLOT_STATUSES` 包含 `stopping`；`check_task` 在 stopping 期间返回「正在停止（当前步骤完成后退出）」。

### D3. 部分成果提取：以子会话已落库投影为单一数据源

四条非正常终止路径（协作取消、宽限硬杀、超时、截断）统一在终态收尾处提取：

- 数据源：子会话 assistant 消息的投影 content（`persist_projection` 按步骤持续落库，终止前最后一个完整步骤的产出已在库）。**不**从内存 messages 或 progress 预览另建数据源——协作退出与硬杀兜底两条路径下内存可用性不同，落库投影是唯一保证一致的权威快照。
- 提取：content 中全部 `type=text` parts 按序拼接（有界截断，沿用通知 preview 的字符上限）。
- 写入：`task.result = "【中止前部分产出】\n" + 提取文本`（空产出则保持为空）；`check_task` 返回与父 Agent 通知注入文本携带同一内容。终态通知 preview（≤80 字）SHALL 从提取内容的开头截取——「中止前部分产出」前缀只出现在 `task.result` 与 `check_task` 全文里，不占通知预览的字符预算。
- 执行位置：终态收尾协程（executor → 主 loop）内，先提取、后记录通知，保证通知一定带内容；提取失败（如消息缺失）降级为现状空 preview，不阻塞终止。

`cancel_task` 工具返回改为「已请求停止：<id>（当前步骤完成后停止，可用 check_task 收取部分产出）」——模型不再被告知瞬时取消完成，与实际语义一致。

### D4. 输出截断为一等终止原因（单轮内 sticky）

子 run 终态判定增加截断分支：`_arun` 消费 chunk 时检测**新增** AIMessage 的 `response_metadata.finish_reason == "length"`，置本轮截断标记。截断标记的作用域为**单 run（turn）**：同一轮内后续正常完成的步骤不降级（该轮终态仍为 partial），但**跨轮不传染**——followup 新轮不带旧轮的截断标记，任务终态由最后一轮决定。带截断标记的轮次终态为 `PARTIAL`（finish_reason=`truncated`），`task.result` 同样走 D3 部分成果提取（标注截断原因）。与模型边界的截断告警（`LLMErrorHandlingMiddleware.warn_truncated_tool_calls`）互补：一个管可观测性，一个管终态语义。

### D5. stop API 语义：受理即返回

`SubagentSessionService.stop_run` 不再 `_wait_run` 轮询终态：调用 `cancel` 后返回 **DB run 快照、status 字段覆写为 `stopping`**（响应形状与现有 `RunSnapshot` 完全一致，仅状态值不同；`POST /api/chat/runs/{run_id}/stop` 路径不变，非破坏性）。终态经既有 `bg-task`（task terminal）与 run `run.finished` SSE 事件推送，前端任务卡 / 抽屉按事件更新。DB run 状态保持既有枚举（running → 终态），「正在停止」只在 executor 快照与 stop 响应的覆写值层面表达。

前端消费侧约束：子会话抽屉（`SubagentConversationView`）SHALL 把 `stopping` 视作活跃态——停止按钮保持停止形态（或禁用）、`runActive` 语义涵盖 `stopping`，输入框的发送行为维持「运行中排队」语义，SHALL NOT 因状态翻转为 stopping 而切换成直发（否则消息会被送进一个正在取消的任务的 followup 队列）。

## Risks / Trade-offs

- **停止不再瞬时**：从「点停止」到终态需等当前步骤（秒级，长工具执行时更久）。缓解：`stopping` 状态即时可见 + 宽限期硬杀兜底（默认 30s 封顶）；排队 / 审批等待中的取消仍瞬时。
- **硬杀兜底仍存在**：宽限期超时的取消与现状行为相同（当前步骤半途而废）。此时部分成果仍可从落库投影回收（终止前最后一个完整步骤），只是比协作路径少最后半步。
- **极长单步骤期间无法协作停止**：astream chunk 粒度是 super-step；单步内无法中断。这是 langgraph 执行模型的固有约束，由宽限期兜底覆盖。
- **静止边界检查延迟退出**：带 tool_calls 的 AI 消息之后必须等工具节点完成才能退出，最坏情况多等一个工具步骤；「正在停止」状态在这段时间内对用户可见。
- **`check_task` 返回变长**：cancelled 携带部分产出后工具返回文本增大，需沿用既有 preview 字符上限（通知侧）与工具输出预算（`tool_output_max_chars`）双重截断。
- **前端状态联合扩展**：任务卡 / 目录 / 子会话抽屉的状态文案映射需识别 `stopping`，抽屉的活跃判定（停止按钮形态、输入框发送语义）需同步扩展；旧快照无该值不受影响。
- **截断跨轮不传染**：多轮任务早轮截断、末轮正常完成时任务终态为 completed——末轮完整即视为交付完整；早轮的截断事实保留在对应 child run 的历史里。
