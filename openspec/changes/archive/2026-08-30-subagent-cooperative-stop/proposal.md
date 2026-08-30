## Why

用户或主 Agent 停止一个后台子 Agent 时，当前的取消是「硬杀」：直接 cancel 执行协程，正在进行的步骤（模型调用 / 工具执行）半途而废，产出既不进子会话历史，也不会回传主 Agent。实测后果是：跑了数分钟的调研被停止后全部作废——终态通知只有「已取消 · N 步」，`check_task` 只返回一句 `cancelled`，主 Agent 写最终报告时拿不到任何部分成果；而子会话投影其实一直持久化到取消前一刻，数据在库里，只是没有回收通道。同时「点停止」到「真正停下」没有中间态，UI 与 API 均无法表达「正在停止」。

取消的正确语义应当是：**信号同步发出、执行协作停止、部分成果显式回收**。停止一个 Agent 不等于丢弃它已完成的工作。

## What Changes

- **协作式停止取代硬杀**：`cancel` 不再 cancel 执行协程，而是同步置位停止请求（任务进入 `stopping` 中间态）；执行循环在**静止边界**（工具节点完成、无未应答工具调用的快照点）检查停止请求，让当前步骤完整结束、投影与 checkpoint 落库后优雅退出。
- **状态机增加 `stopping` 中间态**：`running → stopping → cancelled`；取消请求即时可见（UI 显示「停止中」），终态经正常收尾路径到达；对 `stopping` 任务重复停止幂等。
- **部分成果回收为一等行为**：任务因取消 / 超时 / 截断等非正常原因终止时，从子会话已持久化投影中提取全部文本产出，作为 `task.result` 兜底；终态通知、`check_task`、父 Agent 收取素材三条链路统一携带，格式为「终止原因 + 中止前已产出的部分内容」。
- **输出截断升级为一等终止原因**：模型输出被 provider 以 `finish_reason=length` 截断（含参数被拦腰截断的工具调用）时，run 终态 SHALL 标记为截断（partial），不得伪装为正常 completed；截断标记单轮内 sticky（后续正常步骤不降级），跨轮不传染（任务终态由最后一轮决定）。
- **停止 API 语义调整**：`POST /api/chat/runs/{run_id}/stop` 对子 Agent run 返回 DB run 快照、status 覆写为 `stopping`（已受理、收尾中，响应形状不变），不等待终态；终态经既有 SSE 事件（task terminal / run.finished）推送。前台 `check_task` 在 `stopping` 期间返回「正在停止」。
- 超时 watchdog 触发的终止同样走协作停止路径，同样回收部分成果（终止原因为 timed_out）。

## Capabilities

### New Capabilities

（无——本变更全部落在既有能力的需求演进上）

### Modified Capabilities

- `agent-background-tasks`: 取消语义从硬杀改为「同步信号 + stopping 中间态 + 步骤边界协作退出」；新增部分成果回收要求（取消 / 超时 / 截断终止均携带中止前产出）；新增输出截断作为一等终止原因；`check_task` 对 stopping / cancelled 的返回内容升级。

## Impact

- 后端：`noesis/agents/subagents/executor.py`（cancel 路径、`_arun` 静止边界停止检查、状态机、部分成果提取）、`noesis/agents/subagents/tools.py`（`check_task` / `cancel_task` 返回文案）、`noesis/services/subagent_session_service.py`（`stop_run` 不再等待终态、状态覆写）、`noesis/agents/subagents/notifications.py`（取消通知携带部分成果 preview）。
- SSE：不新增事件类型；复用既有 `bg-task`（task 快照，状态含 `stopping`）与 run `run.finished` 事件，兼容现有前端。
- 前端：任务卡、目录面板与**子会话抽屉**需识别 `stopping` 状态（「停止中」文案与状态点；抽屉的活跃判定 / 停止按钮形态 / 输入框发送语义同步扩展，避免 stopping 期间误直发）；`agent-background-tasks` 的任务状态联合类型扩展。无破坏性 API 变更（stop 响应仍为 RunSnapshot 形状，仅 status 值新增覆写）。
- 数据：无新表；子会话 assistant 消息（投影）成为部分成果的既有数据源。
- 兼容性：`stopping` 为新增状态值，旧客户端把它当未知状态显示原文即可，不破坏解析；`check_task` 对 cancelled 的返回从单行文本变为携带部分内容，模型侧为纯增益。
