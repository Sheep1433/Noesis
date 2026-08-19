# Design: SuperAgent 全异步后台子 Agent

## 1. 方案选型

| 方案 | 执行位置 | 生命周期 | 结论 |
|------|---------|---------|------|
| deepagents `AsyncSubAgentMiddleware` | 远程 langgraph-api | 服务器托管 | ❌ 必须部署 Agent Protocol 服务；`url=None` 的 ASGI 传输仅在 langgraph-api 进程内可用，FastAPI 进程不适用 |
| deer-flow 模型（工具内轮询） | 进程内后台 + 工具阻塞等待 | tool call 内 | ❌ 执行异步但模型视角仍同步，主 Agent 无法继续响应用户 |
| **Noesis 进程内全异步** | 专用守护线程独立事件循环 | 归属 session，跨 run | ✅ 无新服务；主 Agent 立即返回；代价是收结果需要通知机制补偿 |

关键机制对照 Claude Code：其后台 Agent 用**完成通知推送**（模型不轮询）+ `SendMessage` 中途调整。本设计对齐这两点，替代「模型 check 轮询」。

## 2. 执行层（已实现，决策记录）

### 2.0 资源绑定隔离 loop（自查修复）

后台 worker 的 **LLM 客户端（httpx）与 checkpointer（psycopg 连接池）都绑定创建时的 event loop**；隔离线程 loop 复用主 loop 创建的实例会 cross-loop 报错。因此 worker 不在装配期编译，而由 `worker_factory`（async）在隔离 loop 内**惰性编译**：`create_isolated_checkpointer()` 为隔离 loop 建独立连接池（与主 saver 共用 checkpoint 库、实例独立）；`get_llm` 亦在工厂内调用。注册表操作（get / list / submit_decisions / cancel / send_message）为 **staticmethod**——Service 层经类名直调，不依赖任何 executor 实例存活。

### 2.1 隔离事件循环

后台任务在专用守护线程的常驻 `asyncio` loop 上运行。它是**主 run 任务树之外的执行**：主 run 的 producer task 结束、取消、下一轮开始都不影响它。这是「任务活过调用方」的进程内实现，等价于 langgraph-api 服务器提供的托管能力。

### 2.2 注册表与状态机

进程级 `_TASKS: dict[task_id, _TaskEntry]`，`BackgroundTask` 状态机：

```
running ──┬─→ completed
          ├─→ failed / timed_out（watchdog / 异常）
          ├─→ awaiting_approval ──decisions──→ running ──→ …终态
          └─→ cancelled
```

- 并发上限按会话计（默认 3），`start` 时预检。
- 任务超时（默认 900s）：watchdog cancel 执行 future。
- 审批超时（复用 `HitlConfig.ask_timeout_seconds`，24h）：自动按 reject 续跑，对齐主 run HITL 超时语义。
- thread_id = task_id：子 Agent 用共享 checkpointer 的独立 thread，与主对话隔离。

### 2.3 事件通道取舍

曾考虑 executor 终态事件经 `get_stream_writer` 推 SSE，验证后放弃：Noesis 只消费 `astream_events`，writer 写入不进该管道；隔离线程无回调上下文，`dispatch_custom_event` 也不可达。v1 以 API 轮询（BgTaskPanel 5s）兜底；完成通知走第 4 节的注入机制，不走 SSE。

## 3. Steering（中途调整）

### 3.1 语义

Claude Code `SendMessage` 的等价物：指令排队，子 Agent 在**下一个回合边界**处理。LangGraph 下的回合边界 = **下一次模型调用**，注入点选中间件的 `wrap_model_call` seam（与 DynamicContext / DurableContext 注入同构，已在栈内有先例）。

### 3.2 组件

```
send_message(task_id, text)
   → steering registry（模块级 dict[task_id, deque]，线程安全，上限 10 条溢出丢最旧）
   → SteeringMiddleware（挂在 task-worker 栈）
       wrap_model_call:
         thread_id = runtime.config.configurable.thread_id
         messages = pop 全部待注入指令
         request.override(messages=[*request.messages,
             HumanMessage(f"[用户策略调整] {text}")])
```

- 指令注入后即消费，不重复出现在后续轮次。
- 任务 `awaiting_approval` 时指令照常入队，resume 后第一次模型调用生效。
- 终态任务 `send_message` 返回错误说明。
- 超长工具调用期间的指令要等其返回后才被模型看到——与 Claude Code 回合边界语义一致，接受。

### 3.3 暴露面

模型侧 `send_message(task_id, message)` 工具；用户侧 `POST /bg-tasks/{id}/message`（经 BgTaskService 归属校验）。

## 4. 完成通知

### 4.1 语义与注入点

后台任务**终态**（completed / failed / timed_out / cancelled / 审批超时自动拒绝）时，向会话级待送达队列写入一条通知。该会话**下一次 run 启动时**（`exec_query` 组装本轮输入前）drain 队列，作为系统前缀拼入：

```
[系统通知] 后台任务 bg-xxx 已完成（xxx 小结预览 80 字…），可用 check_task 收取完整结果。
[系统通知] 后台任务 bg-yyy 失败：…。可 list_tasks 查看或重新委派。
```

- 通知只注入一次（drain 即清）；进程内队列，与注册表同生命周期（重启同丢，一致）。
- `awaiting_approval` **不**注入模型通知：模型无法审批，审批触达走用户面板（BgTaskPanel 轮询 + 审批卡）。

### 4.2 主动唤醒的边界（明确不做）

无用户消息时 Noesis 不存在模型调用入口（定时任务除外）。因此通知是**下次交互时可见**，不主动唤醒 run——这是与 Claude Code 推送通知的架构差异，v1 接受并以前端轮询徽标兜底；写入「非目标」，避免后续误判为缺陷。

## 5. Prompt 语义（SUPER_AGENT_QA）

委派章节改写要点：

1. **何时委派**：不变（上下文隔离判据 + 独立并行子线）。
2. **怎么委派**：`start_task` 立即返回；启动后**继续当前工作或直接回复用户**，不要原地等待。
3. **怎么收果**：系统会在任务终态时注入 `[系统通知]`；收到通知后 `check_task` 收取。**禁止启动后立刻反复 check**（浪费轮次）；确需中途了解可 `list_tasks`。
4. **怎么调整**：方向跑偏用 `send_message` 下发修正，之后继续其他工作。
5. **不委派什么**：依赖前序结果、需同上下文连续推理的链——留在主上下文（原有规则保留）。
6. 并行：独立子线各自 `start_task`，受会话并发上限约束（超限时工具返回错误说明）。

## 6. 风险与回归

| 风险 | 缓解 |
|------|------|
| 模型启动后忘记 check | 通知注入 + prompt 明令禁止反复 check；list_tasks 兜底 |
| steering 注入破坏子 Agent 消息序 | 注入为追加 HumanMessage（LangGraph 合法序）；中间件单测锁定 |
| 注册表内存泄漏（终态任务永不清） | 注册表按会话列表查询；清理策略：终态任务保留最近 N 条（后续观测驱动，v1 不做定时清理） |
| 并发上限误伤（awaiting_approval 占位） | 上限计 running + awaiting_approval；审批超时自动释放 |
| 通知注入污染用户消息原文 | 前缀以独立标记包裹，落库时只存用户原文（注入发生在 run 输入组装层，不写 DB） |

## 7. 测试策略

- executor 契约（已有 7 例）：补 steering 入队/消费/终态拒绝、通知 drain 一次性、awaiting_approval 期间入队 resume 后生效。
- 中间件：SteeringMiddleware 注入位置与消费语义单测（真实 create_agent + FakeModel）。
- prompt：断言异步委派关键词（start_task / 系统通知 / 不等待）存在。
- 通知注入：`exec_query` 组装层单测（不启动真实 run）。
