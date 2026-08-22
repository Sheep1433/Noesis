# Design: SuperAgent 子 Agent 会话（最终设计）

> 本文的最终决策以 [`docs/architecture/subagent-sessions.md`](../../../docs/architecture/subagent-sessions.md)
> 为准。下方早期章节记录过渡期的 BgTask 执行方案，不能作为新的产品数据模型或前端协议。

## 最终决策（2026-08-21）

**subagent = 子 `TChatSession(parent_id=主会话)` + 标准 `TAgentRun` + 标准 `TChatMessage`；只有 shell 才是后台 Job。**

- `run_in_background` 只控制父 Agent 是否等待，不表示 one-shot/continuable，也不改变 child session 身份。
- 首轮和补充要求都是 child session 的普通 user message；每一轮创建标准 `TAgentRun`。
- checkpointer 只负责 LangGraph 执行恢复，不能作为产品消息读模型；禁止 checkpoint → DB 的异步镜像作为长期方案。
- 详情打开时加载标准会话历史并订阅 `/sessions/{id}/events`；关闭立即退订，不使用 progress 触发全量重拉。
- 主 Agent 与子 Agent 共用同一个 `ConversationView`、MessagePartsRenderer、Markdown、审批和输入框。
- 父会话只展示带 `child_session_id` 引用的卡片和轻量状态；完成通知必须包含 Agent label、状态、轮次、步骤、耗时。
- 现有 BgTask 子 Agent 路径属于过渡实现，迁移完成后删除；shell job 可以保留独立执行器。

## 0. 参照系与结论修正

| 参照 | 机制 | 采纳结论 |
|------|------|---------|
| deer-flow SubagentExecutor | 隔离 loop + 进程级注册表 + 状态机 + 并发上限 | ✅ 执行层照搬（已实现） |
| deer-flow task 工具 | 后台执行 + **工具内轮询等待**（模型视角同步） | ⚠️ 部分采纳：前台等待模式采用「执行后台化 + 工具等待终态」，但默认不等待 |
| Claude Code 后台 Agent | 完成通知推送（回合边界）+ SendMessage 中途调整 + 可展开子 Agent 视图 | ✅ 采纳通知驱动收果与 followup 调整 |
| 通用参数化委派模式 | 单工具 + `run_in_background`，**选择权归模型**，依赖结果选前台 | ✅ 采纳参数化同异步（修订初版「同步整体退役」的结论） |
| 持久子会话模式 | 子任务有独立持久对话；点开=读完整记录；followup=追加 turn（活则排下轮，闲则冷恢复）；人/模型同路径 | ✅ 采纳 followup-turn 语义与子会话查看；底层用现成 checkpointer thread 实现持久层 |

初版三处设计被修订：

1. **「同一 Agent 不该同异步并存」→ 错**。单工具 + 参数不产生选择混乱，且依赖链委派确实需要前台等待。改为 `run_in_background` 参数。
2. **SteeringMiddleware 注入式调整 → 弱于 followup-turn**。注入只影响当前轮的下一次模型调用；followup 是子会话的完整新 turn（子 Agent 带全部历史接续推理，可多轮工具调用）。steering 退役，send_message 升级为 followup。
3. **过程展示以轮询摘要为主 → 升级为子会话查看为主**。子 Agent 完整历史在 checkpointer thread 里，读取渲染即可，摘要轮询降级为概览。

## 1. 方案选型（执行位置）

| 方案 | 执行位置 | 结论 |
|------|---------|------|
| deepagents `AsyncSubAgentMiddleware` | 远程 langgraph-api | ❌ 必须部署 Agent Protocol 服务；`url=None` 的 ASGI 传输仅在 langgraph-api 进程内可用 |
| deer-flow 模型（工具内轮询为默认） | 进程内后台 + 工具阻塞等待 | ❌ 默认同步拖住主 Agent；但其「执行后台化 + 工具等待」作为前台模式保留 |
| **Noesis 进程内全异步 + 参数化前台** | 专用守护线程独立事件循环 | ✅ 默认后台；前台 = 同一执行路径 + 工具 await 终态 |

关键机制对照 Claude Code：其后台 Agent 用完成通知推送 + SendMessage 调整。本设计对齐「通知收果 + followup 调整」两点；主动推送受「无用户消息不唤醒」架构限制（见 §7），以前端轮询兜底。

## 2. 执行层（已实现，决策记录）

### 2.0 资源绑定隔离 loop

后台 worker 的 LLM 客户端（httpx）与 checkpointer（psycopg 连接池）都绑定创建时的 event loop；隔离线程 loop 复用主 loop 创建的实例会 cross-loop 报错。因此 worker 不在装配期编译，而由 `worker_factory`（async）在隔离 loop 内**惰性编译**：`create_isolated_checkpointer()` 为隔离 loop 建独立连接池（与主 saver 共用 checkpoint 库、实例独立）；`get_llm` 亦在工厂内调用。注册表操作（get / list / submit_decisions / cancel / send_message）为 **staticmethod**——Service 层经类名直调，不依赖任何 executor 实例存活。

### 2.1 隔离事件循环

后台任务在专用守护线程的常驻 `asyncio` loop 上运行。它是**主 run 任务树之外的执行**：主 run 的 producer task 结束、取消、下一轮开始都不影响它。这是「任务活过调用方」的进程内实现，等价于 langgraph-api 服务器提供的托管能力。

### 2.2 注册表与状态机

进程级 `_TASKS: dict[task_id, _TaskEntry]`，`BackgroundTask` 状态机：

```
running ──┬─→ completed ──send_message──→ running（冷恢复续话）
          ├─→ failed / timed_out（watchdog / 异常，不可续）
          ├─→ awaiting_approval ──decisions──→ running ──→ …终态
          └─→ cancelled（不可续）
```

- 并发上限按会话计（默认 3），start 时与插入同锁预检（无 TOCTOU）。
- 任务超时（默认 900s）watchdog cancel 执行 future；审批超时（复用 `hitl.ask_timeout_seconds`）自动按拒绝续跑。
- thread_id = task_id：子 Agent 用隔离 checkpointer 的独立 thread——**这同时是子会话持久层**（见 §5），无需专门建 Session 存储即由 checkpointer 获得。

### 2.3 前台等待（run_in_background=false）

执行路径与后台完全相同（隔离 loop、同一注册表、同一超时）；差别仅在工具返回时机：

```python
task_id = executor.start(worker_factory=..., ...)
if not run_in_background:
    result = await asyncio.wrap_future(entry_future)  # 跨 loop 等待终态
    return format(result)                              # 终态文本作为工具返回值
return f"后台任务已启动：{task_id} ..."
```

- `asyncio.wrap_future` 把 concurrent.futures.Future 桥接为当前 loop 的 awaitable，不阻塞事件循环。
- 前台等待期间主 run 的超时/取消语义不变（工具 await 被取消时任务继续后台跑，v1 接受：模型下次可 check 收果）。
- 审批打断在前台模式下的表现：await 的是终态，awaiting_approval 期间工具持续等待，用户面板审批后任务续跑至终态、工具返回。若审批悬置超过主 run 时长，主 run 先结束（SSE 断），任务后台继续——前台等待退化为后台语义，可接受。

## 3. Followup-turn 续话（替代 steering 注入）

### 3.1 语义（followup-turn）

`send_message(task_id, message)` = **子会话追加一个 turn**：

- 任务 **running**：消息进入该任务的 followup 队列（FIFO，上限 10）；当前 turn 的 ainvoke 结束后，executor 检查队列，非空则同 thread `ainvoke({"messages": [HumanMessage(message)]})` 链式开新 turn，任务保持 running；循环直到队列清空，任务转终态。
- 任务 **awaiting_approval**：消息入队；审批 resume 完成本 turn 后由同一条链消费。
- 任务 **completed**：冷恢复——同 thread 追加 HumanMessage 开新 turn，任务回到 running，结束后更新结果。thread 即持久子会话（可续任务模型）。
- 任务 **failed / timed_out / cancelled**：不可续，返回错误说明（模型重新委派）。

### 3.2 与注入式 steering 的取舍

注入式（初版 SteeringMiddleware）：消息在当前轮下一次模型调用生效，子 Agent 无新 turn、不能自主多轮推进。followup-turn：子 Agent 拿到完整新 turn（可多轮工具调用再交付），且 completed 任务可续命。后者覆盖前者全部场景且语义更清晰，SteeringMiddleware 与 steering.py 队列退役。

### 3.3 暴露面

模型侧 `send_message(task_id, message)` 工具（不变）；用户侧 `POST /bg-tasks/{id}/message`（语义同步升级为 followup）。人 / 模型同路径。

## 4. 子会话查看

子 Agent 的完整消息历史持久在隔离 checkpointer 的 `thread_id = task_id` 上。新增读取路径：

- `GET /bg-tasks/{task_id}/messages`：`agent_factory` 产出 worker 后 `aget_state({thread_id})`，把 `state.values["messages"]` 映射为轻量视图项（role / 文本或工具调用名+参数摘要 / 工具结果状态与预览），归属校验同其他 bg API。
- 前端：任务卡展开后渲染完整子会话（模型轮次、工具调用、结果、审批暂停点）；executor 每记录一个新步骤即发布会话级 `progress` SSE，前端据 `progress_count` 重读当前展开项，收起态保留紧凑概览。
- 运行中任务读取 thread state 与隔离 loop 并发访问同一 checkpointer saver：读取走 saver 的连接池（线程安全），不改写状态，只读安全。

## 5. 完成通知（已实现，随后台命令扩展）

终态写会话级待送达队列；下一次 run 组装输入前 drain，以 `[系统通知]` 前缀注入 agent_query（不落库，仅 SUPER_AGENT_QA）。`awaiting_approval` 不注入模型通知（审批走用户面板）。不主动唤醒 run——架构边界，前端轮询兜底（非目标，见 proposal）。

continuation run 的通知消息不伪装用户输入：user 消息落库带 `extra.source_kind = bg_task_notice`，前端渲染为系统通知条；`bg-continuation` SSE 事件携带通知全文供实时插入。该标记为**通用来源标记**——subagent 终态与后台命令终态共用同一管线与同一形态。

## 6. 后台命令（execute run_in_background）

### 6.0 参照与决策记录

| 参照 | 机制 | 采纳结论 |
|------|------|---------|
| dsh tool-bash | bash 工具 `run_in_background`（默认 true）→ `ctx.jobs` 通用任务注册表（kind='bash'）+ `job_output` / `job_kill` / `job_list` | ✅ 单工具+参数与通用任务管线；参数默认改为 false（见 6.2） |
| dsh tool-jobs 完成通知 | busy owner inject / idle owner wakeup + 连续唤醒预算 | ✅ 已由 BgNotifyMiddleware / continuation run 等价实现，直接复用 |
| deepagents execute | 同步阻塞 + timeout 参数（长命令靠 timeout=0 无限等） | ❌ 无后台原语——后台化为 Noesis harness 层职责 |
| Claude Code Bash | `run_in_background`（默认 false）+ BashOutput / KillShell | ✅ 默认值对齐 false；收取复用 check_task 不另开工具 |

deepagents `AsyncSubagentMiddleware`（0.6.12）有异步 subagent，但执行面经 LangGraph SDK 发到 Agent Protocol / langgraph-api 服务器（`url=None` ASGI 模式要求运行在 langgraph-api 进程内）——Noesis 不部署该服务，进程内自建 executor 的决策不变。

### 6.1 工具替换（单工具 + 参数）

`FilesystemMiddleware` 在构造时生成 `execute` 工具（挂在 `middleware.tools`）。Noesis 在装配层**替换**该工具：自建同名 `execute` 工具，schema 增加 `run_in_background`（bool，默认 false）：

- `run_in_background=false`：原样委托给被替换的原工具（前台路径零变化：timeout 校验、输出截断、错误语义均不变）。
- `run_in_background=true`：`executor.start(kind="shell", ...)` 立即返回 task_id。

**必须保留工具名 `execute`**：`interrupt_on["execute"]`（危险 Shell 命令审批）按名匹配，替换后审批继续发生在启动前——HITL 与后台化天然组合，无需额外设计。

### 6.2 参数默认值：false（与 dsh 相反的决策）

dsh 默认 true（新会话无历史习惯）；Noesis 的 `execute` 已上线且模型语义为同步执行，默认 false 保证**存量行为零变化**，长命令由模型显式 opt-in（prompt 教「预期超过几十秒的命令设 run_in_background=true」）。

### 6.3 shell 任务的执行路径

`kind="shell"` 任务**不经 worker 编译**（无 LLM、无 checkpointer thread）：executor 侧直接经当前 session 的 agent backend 执行——`backend.aexecute(command)`（deepagents 的 aexecute 即 `asyncio.to_thread(execute)`，同步 httpx 客户端线程安全，与主 loop 并发使用同一模式）。

- local_shell 模式：命令在宿主机 workspace 跑；docker 模式：经 sandbox-runner docker exec 在**会话容器**内跑。
- **docker 模式下文件工具与 execute 共享容器文件系统**（BaseSandbox 的 ls/read/write 均经 execute）——后台命令写的文件 read_file 立即可见，这是 dsh「shares your workspace」的既有语义，非新增耦合。
- 后台化只发生在**工具层**；`SandboxBackendProtocol.execute` 与文件系统工具（ls/read/write/edit/glob/grep）接口不动。

### 6.4 状态机与收果

- 状态机复用（running → completed/failed/cancelled），但 shell 任务**无 awaiting_approval**（审批在工具调用时已发生）。
- 超时独立：不受 subagent 任务超时（900s）约束——长命令正是后台化动机；`subagents.shell_task_timeout_seconds`（默认 0=不限时），防泄漏靠 cancel_task + 容器生命周期。
- `check_task` 收果：completed 返回 exit code + 有界 stdout/stderr 尾部；`cancel_task` 复用（cancel 后容器内进程由 sandbox-runner 终止或随容器回收，尽力而为）。
- shell 任务不可 `send_message` 续话（命令不是对话性任务），面板可查看；所有 subagent 任务均支持 follow-up。
- 会话沙箱销毁（session 删除 / 沙箱回收）时运行中任务转 failed，错误注明容器回收。

### 6.5 暴露面汇总

模型侧：`execute`（原工具 + run_in_background）、`check_task` / `cancel_task` / `list_tasks` 复用（不新增 job_output / job_kill——现有工具覆盖收取与取消）。用户侧：无新 API（面板/事件流复用）。前端：BgTaskPanel 任务卡对 shell 任务显示命令摘要 + 输出尾部，无 followup 输入。

## 7. Prompt 语义（SUPER_AGENT_QA）

1. **何时委派**：上下文隔离判据 + 独立并行子线（不变）。
2. **同异步选择**：独立可并行的子任务**一起 `start_task` 后继续干活**；**下一步动作依赖该结果**时 `run_in_background=false` 前台等待。
3. **收果**：`[系统通知]` 到达后 `check_task`；禁止启动后反复 check。
4. **调整 / 追加**：`send_message` 向子任务追加指示或后续工作（新 turn 接续推理）；completed 任务可继续追问。
5. **不委派**：依赖前序结果且需在主上下文连续推理的链——留主上下文（不变；需要结果但可隔离的链用前台模式）。
6. **长命令后台化**：预期运行超过几十秒的命令 `execute(..., run_in_background=true)`，继续其他工作，`check_task` 收 exit code 与输出尾部；短命令保持前台同步。

## 8. 风险与回归

| 风险 | 缓解 |
|------|------|
| 前台等待被模型滥用（全选前台退回同步时代） | prompt 明示前台仅用于依赖结果；观测 start_task 调用参数分布 |
| followup 链式 turn 无限循环（子 Agent 每 turn 都收新消息） | followup 队列上限 10 + FIFO 一次性消费；任务总超时 watchdog 覆盖链式 turn 总时长 |
| 冷恢复误续 failed 任务掩盖错误 | 仅 completed 可续；failed/timed_out/cancelled 显式拒绝 |
| 子会话读取与执行并发竞争 | 只读 aget_state，不参与状态机；显示层容忍末尾截断 |
| followup turn 与审批 resume 的 ainvoke 交错 | 链式 turn 只在前一次 ainvoke 返回后调度（同任务串行，注册表 future 单占） |
| 通知注入污染用户消息原文 | 沿用 mention 块注入模式（agent_query 组装层，不写 DB）；continuation 消息带 source_kind 标记，渲染为通知条 |
| 后台 shell 任务泄漏（不限时 + 命令挂死） | cancel_task 可终止；容器生命周期兜底（session 删除/沙箱回收连坐失败）；shell_task_timeout_seconds 可配置上限 |
| execute 工具替换破坏 HITL 审批 | 替换保留工具名 `execute`，interrupt_on 按名匹配不感知替换；契约测试断言审批仍发生在启动前 |
| 后台命令与文件工具并发（docker 共享容器） | 非冲突为设计语义（同一 workspace）；文件工具走自身同步路径不受后台任务影响 |

## 9. 测试策略

- executor 契约：前台等待返回终态文本；followup 链式 turn（running 入队 → 当前 turn 完 → 新 turn 执行）；completed 冷恢复；failed 拒续；审批期入队 resume 后消费。
- 子会话读取：真实 MemorySaver worker 跑完后 aget_state 映射的视图项断言。
- shell 任务契约：run_in_background=true 立即返回 task_id；前台参数缺省委托原工具（超时/截断语义不变）；completed 返回 exit code + 输出尾部；不可 send_message；沙箱销毁转 failed。subagent 任务统一支持 `send_message` follow-up。
- prompt：`run_in_background` / 前台 / 通知 / followup / 长命令后台化关键词断言。
- 通知注入与工具面回归不变。
