# Delta: agent-background-tasks

## ADDED Requirements

### Requirement: 子 Agent 类型注册表与分发

系统 SHALL 提供进程内子 Agent 类型注册表：每个类型为一条不可变声明（`name` 唯一标识、`description` 供模型选择、`worker_factory` 编译配方、可选 `interrupt_on` 审批配置、可选 `model_id` 模型绑定），在 SuperAgent 装配期注册。子 Agent 的模型 SHALL 在装配/配置层按类型解析：`model_id` 绑定的类型以绑定模型编译 worker，未绑定的类型沿用父 Agent 模型；`start_task` SHALL NOT 暴露模型选择参数（运行时只选类型，不选模型）。`start_task` 的 `subagent_type` 参数 SHALL 为必填（缺失即 schema 校验拒绝），SHALL 以注册表为枚举来源：注册成功即出现在工具参数枚举与 system prompt 类型清单中；传入未注册类型时工具 SHALL 返回可诊断错误文本，且 SHALL NOT 创建 child session。装配期检测到重名注册 SHALL 立即失败。类型注册表 SHALL 在构造处集中断言所有 worker 工具集不含后台任务工具（递归委派防线前移至装配期）。

#### Scenario: 按类型委派

- **WHEN** 模型调用 `start_task(description, prompt, subagent_type=T)` 且 T 已注册
- **THEN** 任务 SHALL 以 T 的 worker 配方编译执行，child session descriptor SHALL 记录 type=T

#### Scenario: 未知类型拒绝

- **WHEN** 模型调用 `start_task(..., subagent_type=X)` 且 X 未注册
- **THEN** 工具 SHALL 返回包含 X 与可用类型清单的错误文本
- **AND** SHALL NOT 创建 child session 或 run

#### Scenario: 缺失类型参数拒绝

- **WHEN** 模型调用 `start_task` 未携带 `subagent_type`
- **THEN** 该工具调用 SHALL 因必填参数缺失被 schema 校验拒绝，SHALL NOT 创建 child session

#### Scenario: 类型绑定模型

- **WHEN** 注册类型 T 声明 `model_id=M`，模型调用 `start_task(..., subagent_type=T)`
- **THEN** worker SHALL 以模型 M 编译，child session SHALL 记录生效模型 M
- **AND** `start_task` 的参数 schema SHALL NOT 包含任何模型选择参数

#### Scenario: 未绑定模型沿用父模型

- **WHEN** 注册类型 T 未声明 `model_id`，主 Agent 当前模型为 P
- **THEN** T 的 worker SHALL 以模型 P 编译，child session SHALL 记录 P

#### Scenario: 重名注册失败

- **WHEN** 装配期两个类型声明使用相同 name
- **THEN** 装配 SHALL 抛出异常，Agent 不进入服务

### Requirement: 任务身份进主 Agent graph state

子 Agent 工具面 SHALL 以 middleware 形式挂载（与既有能力 middleware 栈同构），并将任务身份写入主 Agent graph state：`bg_tasks` 按任务 id 合并保存 task_id、child_session_id、subagent_type、description 与最近一次工具交互的状态快照，随主 Agent checkpoint 持久化。任务身份 SHALL 免疫上下文压缩：压缩 SHALL NOT 丢弃 `bg_tasks`。state 定位为投影——任务状态与结果的权威来源 SHALL 永远是执行器注册表（miss 时落 DB），`check_task` / `list_tasks` SHALL NOT 信任 state 快照；终态任务 SHALL 保留在 state 中不删除。

#### Scenario: 压缩后任务清单仍在

- **WHEN** 主 Agent 会话发生上下文压缩且此前启动过后台任务
- **THEN** 压缩后的 state 中 `bg_tasks` SHALL 保留全部任务身份条目

#### Scenario: state 快照过期不误导

- **WHEN** `bg_tasks` 中某任务的快照状态为 running，而执行器中该任务已终态
- **THEN** `check_task` SHALL 返回执行器的实时终态与结果

#### Scenario: 工具写入身份

- **WHEN** `start_task` 成功启动任务
- **THEN** 该工具 SHALL 通过返回 Command 将任务身份写入 `bg_tasks`，同时向模型返回与既往一致的启动提示文本

## MODIFIED Requirements

### Requirement: 后台子 Agent 执行模型

SuperAgent SHALL 通过进程内后台任务执行器 `BackgroundTaskExecutor` 执行委派子任务与后台命令任务：任务在专用守护线程的独立事件循环上运行，生命周期归属 session 而非主 run。执行器为双 kind 运行时（`subagent`：worker 编译 / child session / HITL / followup；`shell`：命令直执行、无落库无 followup），subagent 特性经工厂与端口注入，类型维度对执行器不可见。任务状态机 SHALL 覆盖 running / stopping / awaiting_approval / completed / failed / cancelled / timed_out（`stopping` 为停止已受理、执行收尾中的中间态，仍占并发槽）；每会话并发上限与单任务超时 SHALL 由 `subagents` 配置约束（后台命令任务超时独立约束，停止宽限期 `stop_grace_seconds` 同组配置）。注册表在内存：进程重启 SHALL 丢失运行中任务与 shell job（接受的设计限制）；重启后遗留的活跃 child run SHALL 由启动对账收口为 error（`SUBAGENT_PROCESS_RESTARTED`）；`check_task` 对不存在的 task_id SHALL 返回可诊断提示。任务投影 SHALL 携带 `subagent_type` 字段（shell 任务为 null）。

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

#### Scenario: 任务投影携带类型

- **WHEN** 任一任务进入任务卡投影
- **THEN** 投影 SHALL 包含 `subagent_type`（subagent 任务为注册类型名，shell 任务为 null）

### Requirement: 单工具同异步参数（run_in_background）

委派 SHALL 只有一个工具入口 `start_task`，其参数由模型按依赖关系选择：`run_in_background`（默认 true）——true 立即返回（后台）；false 为**前台等待**——执行仍走同一后台路径（隔离 loop / 注册表 / 超时），工具 await 终态并把终态文本作为工具返回值。前台等待超过 `foreground_max_wait_seconds`（默认 120s）SHALL 自动转为后台并提示稍后 `check_task` 收果（等待经 shield 实现，取消不波及底层任务）。`subagent_type`（必填）按注册表校验与分发（见「子 Agent 类型注册表与分发」）。系统 SHALL NOT 提供第二条同步委派执行路径。

#### Scenario: 前台等待返回结果

- **WHEN** 模型调用 `start_task(description, run_in_background=false)` 且子任务到达 completed
- **THEN** 工具 SHALL 返回子任务的最终小结文本（含 child session id 供前端关联）

#### Scenario: 前台等待超时转后台

- **WHEN** 前台等待超过 `foreground_max_wait_seconds`
- **THEN** 工具 SHALL 返回「已自动转为后台」提示，任务 SHALL 继续后台执行不被取消

#### Scenario: 前台等待期间审批

- **WHEN** 前台任务中途触发工具审批
- **THEN** 工具 SHALL 持续等待至审批后续跑并到达终态；审批触达走用户面板

### Requirement: task-worker 编译契约

后台 task-worker SHALL 经 async 工厂在隔离事件循环内惰性编译：worker 配方 SHALL 由任务所属 `subagent_type` 对应的注册表声明提供（v1 仅 `general`，配方与既有单一 worker 一致），LLM 客户端与 checkpointer 连接池 SHALL 绑定隔离 loop（`create_isolated_checkpointer` 独立建池，共用 checkpoint 库）。task-worker 带 checkpointer 与 SUBAGENT profile 中间件栈，且 SHALL NOT 携带后台任务工具自身（禁止递归委派）。task-worker 与主 Agent SHALL 共享同一沙箱 backend。

#### Scenario: 递归委派防护

- **WHEN** task-worker 的工具集被组装
- **THEN** 其中 SHALL NOT 出现 start_task / check_task / cancel_task / list_tasks / send_message

#### Scenario: 隔离资源绑定

- **WHEN** 后台任务首次执行
- **THEN** 其 LLM 客户端与 checkpointer 连接池 SHALL 在隔离 loop 内创建，不复用主 loop 实例

#### Scenario: 按类型取配方

- **WHEN** 任务以类型 T 启动
- **THEN** worker SHALL 由 T 声明的 worker_factory 编译；执行器状态机 SHALL NOT 因类型差异产生分支

### Requirement: 子 Agent 会话身份

每次 `start_task` SHALL 在父会话下创建一个独立的 child `ChatSession`（`kind=subagent`、`parent_id` 指向父会话、`created_by_tool_call_id` 记录触发调用的模型 tool_call_id），并在同一 launch 事务中写入首条 user message、assistant 骨架与首个标准 `TAgentRun`。launch SHALL 在 child session `extra` 下写入版本化 subagent descriptor（独立键 `subagent`，显式字段 `version`、`type` 与 `model`（该类型解析后的生效模型），读取时校验），供进程重启后重建 worker 的路径按类型与模型取配方。executor 内部 task id 与 child session id SHALL 分离；对外 API 与前端 SHALL 只以 child session id 作为公开身份（目录 `task_id == child_session_id`，`bg-*` 内部 id 不外泄）。左侧会话历史 SHALL 过滤子会话（`parent_id IS NULL`）。父会话软删 SHALL 级联软删 child session 树及其消息，并取消运行中的 run 与后台任务。

#### Scenario: 并行同名独立

- **WHEN** 主 Agent 并行调用两次 `start_task` 且 description 相同
- **THEN** SHALL 创建两个独立 child session / run，`created_by_tool_call_id` 一一对应
- **AND** 父会话卡片 SHALL 各自独立，不按名称合并

#### Scenario: 左侧历史不出现子会话

- **WHEN** 子会话存在且未删除
- **THEN** 会话历史列表 SHALL 不返回该子会话

#### Scenario: 父会话软删级联

- **WHEN** 用户删除父会话
- **THEN** 子会话树与消息 SHALL 级联软删，运行中 run 与后台任务 SHALL 被取消

#### Scenario: descriptor 落库

- **WHEN** `start_task` 以类型 T 启动且 T 的生效模型为 M
- **THEN** child session `extra.subagent` SHALL 记录 `{version: 1, type: T, model: M}`
