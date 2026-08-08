## Context

当前聊天链路有三处权威状态分裂：

1. `backend/noesis_server/services/run_service.py` 的 `RunService.create` 在一个事务中预建 user、assistant 与 run，但 `messages_precreated=True` 使 `QaService` 跳过 `ChatService.get_or_create_session(..., title=...)`，新入口只更新会话时间，没有设置标题。
2. 同一事务中的 user 与 assistant 使用同一个 `created_at`。`ChatService.get_session_messages` 仅 `ORDER BY created_at`，相同毫秒的行顺序由数据库决定；前端 `loadSessionMessages` 按响应数组直接渲染，因此刷新后可能出现 assistant 在 user 前面。
3. 工具调用在 LangGraph event、`LangGraphSseBridge`、`RunProjection`、`AssistantMessageBuilder`、PersistSink 和前端 reducer 间传递。调用失败、进程非零退出、HITL 等待与 Run 暂停目前没有一个覆盖全链路的权威状态，终态/暂停时可能遗留 `running` part。

本变更与 `durable-agent-run-recovery` 配合：后者保证 Run 可查询、可重订阅；本变更保证快照中的消息顺序、标题和工具状态本身可信。当前 Web 前后端共同发布，不维持旧接口的双轨适配。

## Goals / Non-Goals

**Goals:**

- 每个工具 part 使用一个可持久化、可恢复、用户可理解的生命周期状态，并在 Run 暂停或终止时完成收敛。
- 调用异常、命令退出、HITL 和用户取消保留不同机器语义，同时映射为安全、可操作的产品文案。
- 新会话首条消息提交成功后立即拥有稳定标题，刷新和重新打开仍一致。
- 服务端给每条消息分配会话内严格递增序号，历史加载不再依赖毫秒时间或前端角色排序。
- 通过真实 PostgreSQL 与前端 reducer 回归测试覆盖同毫秒写入、刷新恢复和工具终态矩阵。

**Non-Goals:**

- 不保证外部网站、网络环境或命令本身永不失败。
- 不让前端解析 shell 文本、堆栈或 provider 错误来猜测工具结果。
- 不使用 LLM 另发一次请求生成标题；标题采用首条用户文本的确定性摘要，避免增加延迟和失败点。
- 不重写消息树/分支会话产品能力；本次只定义当前会话可见消息的稳定线性顺序。
- 不保留旧客户端对缺少 `message_sequence`、`tool.state` 的新写入兼容分支。

## Decisions

### 1. 工具采用三层语义，`state` 是 UI 与恢复的权威生命周期

现有 `status`（invoke 是否抛异常）和 `outcome`（进程非零退出/超时等）继续承担模型与诊断语义；新增并强制写入 `state`：

| `state` | 是否终态 | 用户语义 |
|---|---:|---|
| `running` | 否 | 正在执行 |
| `approval_pending` | 否 | 等待确认 |
| `succeeded` | 是 | 已完成 |
| `failed` | 是 | 执行失败/连接失败/环境不可用等，细分类见 `errorCategory` |
| `timed_out` | 是 | 执行超时 |
| `rejected` | 是 | 用户已拒绝 |
| `cancelled` | 是 | 已停止 |

映射集中在后端工具状态模块，不允许前端各组件自行推导：

- invoke error → `failed`，但 `execution_timeout` → `timed_out`；
- invoke success + `outcome=ok|empty` → `succeeded`；
- invoke success + `outcome=command_failed` → `failed`；
- invoke success + `outcome=timed_out` → `timed_out`；
- HITL action request → `approval_pending`；reject → `rejected`；approve 后 → `running`；
- stop/Run 中止时仍非终态的 part → `cancelled`。

选择单独的 `state`，而不是扩大 `status`，是因为 `status=success + outcome=command_failed` 对 Agent 来说是“工具正常返回了失败的命令结果”，对用户却必须显示失败。把二者压成一个字段会丢失模型决策所需信息。

### 2. Bridge 产出终态，RunProjection 做边界 reconcile

正常路径由 `backend/noesis_server/domain/chat/streaming/langgraph_sse.py` 在 `on_tool_end/on_tool_error` 生成带 `state` 的 `tool-output-available`，`AssistantMessageBuilder` 以 `tool_call_id` 幂等更新同一 part。`RunProjection.apply` 必须保留 `state/outcome/exit_code/timed_out/errorCategory/duration_ms` 全部字段，不能像当前实现一样只传部分字段。

边界路径由 `RunProjection` 统一 reconcile：

- 收到 `HitlRequired` 时，action request 对应 part 变为 `approval_pending`；事件队列中此前已到达的 tool output 必须先应用。
- 收到真正终态 `RunCompleted/RunError/RunAborted` 前，所有剩余 `running/approval_pending` part 必须按原因转换为 `cancelled/failed/timed_out/rejected`，不得把非终态写入终态 snapshot。
- `RunPaused(hitl_pending)` 只允许本次 interrupt 的 action parts 为 `approval_pending`；其它没有后续执行者的 `running` part 收敛为 `cancelled`。父 `task` 可保持 `approval_pending`，前提是其子树确实包含本次 interrupt。
- 已终态 part 不接受晚到事件覆盖；重复事件按 `tool_call_id + terminal state` 幂等处理并记录 warning。

PersistSink 继续保存 `RunProjection.builder` 的权威快照；刷新时前端以 snapshot/history replace 当前 assistant，而不是合并客户端旧 `running` 状态。

### 3. 工具失败由卡片展示，无最终回答时显示阻断态

`frontend/src/components/ToolCallCollapse/index.vue` 和 `SubagentCollapse` 按 `state` 展示固定标签。失败卡片默认折叠，但不能隐藏；展开后展示安全短句、必要的 stderr/exit code 与重试建议。堆栈、宿主路径、provider、网络解析细节仅进入日志。

同一 assistant 含 `failed/timed_out/rejected/cancelled` 工具但 Agent 在最后一个工具块之后仍生成可见回答时，不额外显示回答级完整性提示；失败尝试已由对应工具卡片明确展示，Agent 可以继续改用其它工具完成任务。工具调用前的过程文本不视为最终回答。只有最终没有可见回答且存在失败工具时，才显示阻断提示并提供“重试”操作。该阻断态由结构化 parts 派生，不依赖模型自行承认失败。

这比完全隐藏失败更诚实，也比把全部技术错误直接铺开更适合普通用户。具体失败卡片仍保留，支持排障与判断引用是否完整。

### 4. 命令结果只读取结构化执行协议

`execute/bash` 必须返回结构化 `exit_code`、`timed_out`、stdout/stderr。后端只依据这些字段设置 `outcome/state`：退出码非 0 永远是 `failed`，即使命令带 `|| true` 时 shell 最终退出码为 0，系统只能认定 shell 报告的最终成功；若要识别被 `|| true` 吞掉的子命令失败，执行工具必须使用包装协议记录 pipeline/子进程状态，而不能从文本 `command not found` 猜测。

本次实现至少确保所有 Noesis 生成的执行包装不追加吞退出码的 `|| true`，并保留真实 exit code。用户自己显式输入容错 shell 时遵循 shell 语义，不做文本启发式误判。

### 5. 标题在 `RunService.create` 的消息事务内一次性写入

在锁定会话并确认标题仍为 `新对话` 后，使用首条非空 `request.content` 生成标题：去换行、折叠空白、截断到统一上限。user/assistant/run 写入与标题更新在同一事务提交；事务失败则标题也不单独成功。

`CreateRunResponse` 增加 `session_title`，前端收到创建成功响应即更新当前标题和侧栏；刷新后仍以会话 API 为准。SSE `finish.title` 不再作为标题权威来源，可随当前 Web 客户端同步删除，避免“只有流结束才更新”和不同路径各写一次。

保留用户手动改名优先级：只允许从精确默认值转为自动标题，非默认标题永不被新消息覆盖。`ChatService.set_session_title_if_default` 作为唯一规范化/条件更新入口，由 RunService 在同一 DB session 内调用或等价地执行带默认值条件的 UPDATE。

### 6. `message_sequence` 是会话内排序的唯一权威字段

数据模型新增：

- `t_chat_session.next_message_sequence BIGINT NOT NULL DEFAULT 1`；
- `t_chat_message.message_sequence BIGINT NOT NULL`；
- 唯一约束 `(session_id, message_sequence)`；
- 查询索引 `(session_id, message_sequence)`。

所有消息写入先 `SELECT t_chat_session ... FOR UPDATE`，从 `next_message_sequence` 连续分配并递增。`RunService.create` 一次分配两个序号，user 为 N、assistant 为 N+1；其它单消息入口分配一个。这样并发来源、同毫秒写入和 UUID 随机性都不会改变顺序。

`GET /api/chat/sessions/{id}/messages` 始终按 `message_sequence ASC` 返回，并在每项中返回该字段。`before_id` 解析为 cursor 消息的 sequence，查询 `< cursor_sequence`。前端只按响应顺序/sequence 渲染，不做 user-first 二次排序；若新响应缺字段则视为协议错误并给出可恢复提示，不静默猜测。

备选方案及放弃原因：

- `ORDER BY created_at, role`：只能修同一对消息，无法稳定区分同毫秒多轮或多来源消息。
- assistant 时间戳 `+1ms`：伪造时间，仍可能与下一条消息冲突。
- `ORDER BY created_at, id`：UUID 不表达因果顺序。
- 仅靠 `parent_id` 拓扑排序：消息树可表达关系，但分页、分支与无 parent 的入站消息仍需要稳定线性游标。

### 7. 迁移与部署不保留双轨接口

Alembic 迁移按会话回填 sequence：先以 `created_at`、父子关系、role 优先级、id 生成确定性候选序；再校验每个 assistant 的 sequence 大于其 parent user。回填完成后添加 NOT NULL 与唯一约束，并把 session 的 `next_message_sequence` 设为 `max + 1`。

发布顺序为停写维护窗口 → 数据库迁移 → 同版本后端 → 同版本前端。因为用户已明确不保留历史接口兼容，不引入 nullable 长期过渡、双排序或旧 SSE 状态映射。回滚只能整体回滚应用；数据库列可保留，避免破坏已写入顺序数据。

## Risks / Trade-offs

- [迁移时父子关系不完整，旧消息无法完全还原真实先后] → 采用确定性回填并输出审计报告；保证 parent assistant 在 parent user 后，无法证明的旧顺序保持稳定但不宣称还原事实。
- [会话行锁增加同一会话写入等待] → 单会话本来只允许一个 active run；事务内只做序号分配与行插入，禁止在锁内执行 Agent/网络调用。
- [Run 暂停时误把仍在执行的并行工具取消] → 先保证桥接事件 drain 顺序，再在暂停边界 reconcile；加入并行工具 + HITL 的事件顺序测试。
- [父 task 与子工具状态不一致] → reconcile 按树自底向上计算；父 task 等待同一 interrupt 时显示 `approval_pending`，终态时不得残留非终态子 part。
- [失败工具可能被忽略] → 每个失败工具卡片仍保留明确状态和安全详情；仅删除容易误判最终答案完整性的回答级汇总。
- [标题取首条文本可能含命令或附件占位] → 仅使用用户可见文本，规范化空白并截断；纯附件会话使用附件名或保持默认标题，具体规则由测试固定。

## Migration Plan

1. 增加并执行消息 sequence 迁移，生成每会话回填审计统计并校验唯一性、parent 顺序和 `next_message_sequence`。
2. 更新所有消息写入口与查询 cursor；在真实 PostgreSQL 上验证同毫秒和并发写入。
3. 更新 Run 创建事务与 `CreateRunResponse.session_title`，删除前端对 `finish.title` 的权威依赖。
4. 更新工具状态生成、投影、reconcile、落库与 snapshot；再更新前端状态 reducer 和组件。
5. 同版本发布前后端，执行刷新恢复、HITL、stop、网络失败、命令非零退出和子 Agent 嵌套验收。
6. 若应用需回滚，整体回滚前后端；保留新增 sequence 列和已分配值，不回退为时间排序。

## Open Questions

无。标题采用确定性首条消息方案；消息排序采用服务端 sequence；工具失败默认对用户可见，均作为本变更的确定决策实施。
