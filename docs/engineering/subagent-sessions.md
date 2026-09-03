# 子 Agent 会话架构（最终方案）

## 结论

子 Agent 不是一种特殊的后台任务，而是父会话下的一次独立 Agent 会话。

`run_in_background` 只表示父 Agent 是否等待这次调用的结果，不改变会话身份；它不再决定
`one_shot`、`continuable` 或其它对话模式。每次 `start_task` 都创建一个新的子会话，同一个
子会话可以继续发送多轮消息，直到用户或系统显式结束它。

后台执行器只能是运行时实现细节，不能成为产品数据模型、API 命名或前端展示模型。

运行器与会话用例通过 `subagent_runtime_port` 注册窄端口通信，避免 executor 反向依赖产品 Service；
目录与 shell job 由 `AgentCatalogService` / `ShellJobService` 暴露给 API。

## 为什么推翻现有 BgTask 中心方案

当前方案把 `BackgroundTask` 同时当作执行状态、会话身份、消息历史入口和 UI 卡片。这会产生四个
长期问题：

1. 主 Agent 和子 Agent 使用两套消息渲染与 SSE 协议，Markdown、工具调用、审批和耗时都会漂移。
2. 一个任务的多轮对话只能通过 followup 队列拼接，无法自然表达轮次、消息父子关系和重新打开。
3. 任务列表、系统通知、子会话详情互相引用 task id，最终用户看不出“哪个 Agent、哪一轮、哪条结果”。
4. 只在抽屉打开时补拉快照，运行过程不是事件流；关闭后又丢失实时性，重新打开还要重新拼装状态。

因此不再继续扩展 `BgTaskPanel`、`BackgroundSubagentCollapse` 或 `/bg-tasks/*/messages`，这些属于过渡层，
最终应删除。

## 领域模型

### AgentProfile

稳定的 Agent 配置，不代表一次运行。字段建议：

```text
profile_id       task-worker / researcher / shell
label            用户可见名称
icon             用户可见图标
model            使用的模型
capabilities     tools / approval / followup
```

### ChatSession

一次独立的对话实例。现有 `TChatSession` 直接承担该职责：

```text
id               唯一会话 ID
parent_id        父会话 ID；根会话为 NULL
agent_profile    extra 中的稳定 profile
origin           root | subagent | scheduled
title            用户可见标题
created_at       创建时间
updated_at       最近活动时间
```

同名 Agent 的每次调用都创建不同的 `ChatSession`，但可使用同一个 `AgentProfile`。历史会话只展示
根会话；父会话的 Agent 目录展示其直接子会话并按创建时间排序。

### AgentRun

一次 turn 的生命周期。现有 `TAgentRun` 复用，不另造 BackgroundRun：

```text
run_id           唯一运行 ID
session_id       子会话 ID
status           queued | running | hitl_pending | completed | failed | cancelled
started_at       开始时间
finished_at      结束时间
last_sequence    已发布事件序号
snapshot         恢复信息
```

一个子会话可以有多个 `AgentRun`。补充要求就是新建一条 user message 和一个新的 AgentRun，
而不是向旧 BgTask 的字符串队列追加文本。

### ChatMessage

所有会话共用 `TChatMessage` multipart 格式。用户消息、assistant 消息、reasoning、tool call、tool
result、approval 均按主 Agent 的消息协议落库和渲染。系统状态不伪装成 user message，而是
`extra.source_kind = child_status` 的结构化投影。

### DelegationReference

父 Agent 的工具消息保存一次委派引用：

```json
{
  "type": "child_session",
  "child_session_id": "...",
  "profile_id": "task-worker",
  "label": "政策检索",
  "status": "running",
  "turn_count": 1
}
```

前端据此展示紧凑卡片，不再从工具输出文本正则猜 task id。

## 后端执行和事件

### 创建与继续

1. `start_task(description, run_in_background)`：在父会话下创建 child session，写入首条 user message，
   创建首个 AgentRun，然后启动 worker。
2. `run_in_background=false`：父 Agent 等待该 AgentRun 终态并接收结果；child session 身份不变。
3. `run_in_background=true`：父 Agent 立即继续；子会话独立运行。
4. `send_message(child_session_id, message)`：为同一个 child session 创建新的 user message 和 AgentRun，
   可用于 running、hitl_pending、completed；失败或取消的会话需要显式重新创建。
5. `check_task` 仅作为模型侧收取状态的兼容工具，产品 UI 不依赖它来拼消息。

### 统一事件流

新增通用接口：

```text
GET /api/chat/sessions/{session_id}/events?after_sequence=N
```

事件按会话内严格递增序号发布（与主聊天同一帧词表，见
`platform/chat-streaming.md` §4.2b），至少包含：

```text
run-snapshot
run.started
message-start
text-delta / text-end
reasoning-start / reasoning-delta / reasoning-end
tool-input-start / tool-input-available / tool-output-available
retrieval-results-available
stats-update
context-update
approval.required / approval.resumed
run.finished（唯一终态标记，后随 [DONE]）
```

流式 delta / 实时统计为 transient（不占序号、只投在线订阅）；内容投影由
前端 `messageParts` appenders 从帧组装，权威恢复走 `getAgentRun` 快照 +
durable 重放（`message.updated` 全量投影事件已退役）。

打开子 Agent 详情时：先拉历史消息和当前 run 快照，再从最后序号订阅 SSE；事件直接增量更新当前
视图。关闭抽屉立即退订。没有打开详情时，只订阅父会话的轻量 `child_session` 状态事件；不传输
子 Agent 的正文和工具输出。

事件需要可重放：短期可使用进程内 ring buffer + DB 消息序号，最终以 `TChatMessage.message_sequence`
和 `TAgentRun.last_sequence` 作为断线恢复游标。禁止“每个 progress 事件重新 GET 全部消息”作为主路径。

### 审批

审批事件属于 child session 的结构化事件，审批卡复用主 Agent 的 `HitlApprovalCard`。审批通过后
继续同一个 AgentRun；若审批需要新用户指令，则创建新的 turn，不向旧 task 字符串队列写入内容。

### 通知

父会话只接收状态投影：

```text
子 Agent「政策检索」已完成 · 2 轮 · 14 步 · 20s
```

通知必须带 child session id 的可点击引用、Agent label、状态、轮次和耗时。失败、超时、取消分别
使用不同标题和颜色。通知不复制完整结果；点击后进入该 child session 详情。

自动化/定时任务的 user、assistant 消息仍写入关联会话，但不会更新根会话的最近活动时间，避免定时任务
自动把会话抢到左侧历史列表顶部；用户主动打开或继续该会话后，才恢复正常的最近活动排序。

## 前端结构

### 一个会话视图

实现一个 `ConversationView(sessionId, mode)`，主 Agent 和子 Agent 共用：

- Markdown / reasoning / tool call / tool output 渲染
- user / assistant 气泡
- run elapsed、turn count、step count、token usage
- HITL 审批卡
- 输入框和 follow-up 发送
- SSE 连接、断线重连、游标恢复

`mode` 只控制容器：根会话占主区域，子会话放宽抽屉；不能复制一套消息组件。

### 父会话中的子 Agent 卡片

卡片只展示：Agent 图标、label、状态、当前轮次、步骤数、耗时、最后一句摘要。每次调用一个卡片，
不按名称合并。点击卡片打开同源 `ConversationView` 抽屉；并行调用仍保持独立卡片。

### Agent 目录

父会话顶部显示子会话目录，支持嵌套层级和状态筛选。目录项打开同一个抽屉，不跳转到另一套
“后台任务详情”页面。根历史列表过滤 `parent_id IS NULL`，避免子会话污染左侧历史。

### 抽屉

统一使用一套响应式宽度：桌面 760px（可拖拽到 920px），移动端 96vw。点击卡片、Agent 目录和
输入框引用的子会话，全部调用同一个 drawer composable 和关闭退订逻辑。

### 统一 run 管道（主/子同一条执行内核）

主 Agent 与子 Agent 的 run 执行共用同一条管道：`runtime/stream.py` 消费
`astream_events` → `RuntimeEventMapper`（raw event → typed RunEvent）→
`LangGraphSseBridge` + `AssistantMessageBuilder` 聚合（usage 累计、上下文快照、
HITL 投影、终态 payload）。主链路经 delivery 序列化为 SSE 帧；子 Agent 的
executor（生命周期包装：任务注册表、隔离事件循环、watchdog、followup 队列）
消费同一管道：投递走统一投递内核（`chat/runs/delivery_bus.py`，主/子同一
语义实现），投影经 `AgentRunRepository.save_checkpoint` 落库（与主链路同一
事务实现）。

由此主/子能力同源：usage 双口径（父会话当轮「主+子合并」/ 各会话自身
`extra.usage` 终态落库）、上下文快照（bridge 模型调用边界统一提取）、
推理档位（创建时在父上下文捕获继承，followup 逐 turn 覆盖 model_id +
reasoning_effort，参数变化即重编译 worker）。同一 run 级能力不得在主/子
链路各写一份实现。

## 与 DeepSeek Harness 的关系

DeepSeek Harness 的关键优势是：子 Agent 是独立 durable Session，有 parent/child 关系、独立事件
流和完整会话 UI；同名 Agent 只是共享 profile，不会合并成一个运行实例。Noesis 应采纳这个
领域划分和目录模型。

不同点：Noesis 已有 `TChatSession`、`TChatMessage`、`TAgentRun` 和 LangGraph run stream，因此不
需要复制 Harness 的 SessionManager 或另建前端消息协议；应把现有会话/Run/SSE 统一起来。Shell
后台命令仍是 job，不创建对话型 child session。

Harness「子 Agent 经 `ctx.agents.create/resume` 复用同一 runtime」的形态已在本仓库落地为
统一 run 管道（见上节）：executor 只保留生命周期差异，run 管道与主 Agent 一份实现。

## 过渡层清理（已删除）

- `t_bg_task` 持久化及整套快照存储（`BgTaskStore` 协议、repository、启动对账接线）：执行面完全在进程内，重启即丢，与 dsh `ctx.jobs` / deer-flow 注册表同构；subagent 的产品数据由标准会话/Run/消息表承载，shell job 不持久化
- `/bg-tasks/{id}/messages` 与 `/messages/stream` API 及 checkpoint thread 读路径（checkpointer 只负责执行恢复）
- 从 tool output 正则提取 child id 的逻辑（卡片按 `child_session_id` / `created_by_tool_call_id` 结构化关联）
- `progress_count` 驱动的全量消息重拉
- `one_shot`、`continuable`、任何 continuation mode 参数

`BackgroundSubagentCollapse` 已收敛为父会话内的子 Agent 卡片（点击打开统一的子会话抽屉），不再有独立消息渲染；`BackgroundTask` 仅剩进程内执行器的实现细节。

保留但下沉为执行实现：

- 隔离事件循环或等价 worker runtime
- 并发限制、超时、取消、审批恢复
- `run_in_background` 作为工具等待策略
- `execute(..., run_in_background=true)` 的 shell job

## 实施顺序和验收标准

1. 先实现 child session/run 创建与消息落库，补充 parent/child API 和根历史过滤。
2. 实现通用 session event stream，先让主 Agent 和 child session 都能消费同一协议。
3. 把 `start_task`、follow-up、审批迁移到 child session；删除字符串 followup 队列作为产品接口。
4. 抽取共享 `ConversationView`，替换两个旧子 Agent 组件；父会话只保留卡片和状态投影。
5. 删除 BgTask 专用 API/UI，保留 shell job 的独立 job API。
6. 做断线恢复、并行调用、同名 Agent、多轮追问、审批、失败/超时/取消、移动端抽屉回归测试。

验收必须满足：

- 子 Agent 打开时正文和工具过程实时流式；关闭时不订阅正文流。
- 刷新或断线后按游标恢复，不重复消息、不丢消息。
- 主 Agent 与子 Agent 的消息视觉和 Markdown 行为一致。
- 同名 Agent 每次调用独立展示，但可在同一父会话目录下聚合查看。
- 左侧历史不出现 child session。
- 任何终态通知都能指向具体 Agent 和 child session，而不是泛化的“后台任务已完成”。
