# subagent 类型分发与任务运行时身份澄清

## Why

`start_task` 目前只有一种隐式的子 Agent：worker 的系统提示词、工具集、backend 策略全部硬编码在 SuperAgent 装配层。要增加一个种类（例如检索型、分析型、运维型子 Agent），没有类型参数、没有注册表、没有分发点——只能复制装配代码。同时存在两个结构债：任务身份（task_id / child_session_id）只存在于消息历史里，上下文压缩后模型可能丢失手上任务的清单；执行器名为 `BackgroundSubagentExecutor` 却同时承载 subagent 与 shell 两类后台任务，命名与语义错位。

## What Changes

- 新增**类型注册表**：`subagent_type → worker 编译配方`（描述、worker 工厂、审批配置、可选**模型绑定**）的进程内注册，装配期注册、重名即失败；v1 仅注册单一 `general` 类型，行为与现状逐字一致（迁移安全网）。模型绑定在配置层按类型解析，`start_task` 不暴露模型选择参数——注册表即未来自定义配置子 Agent（文件式角色）的声明面落点。
- `start_task` 增加**必填** `subagent_type` 参数（枚举自注册表），工具描述自动注入可用类型清单，模型按任务性质选择。
- 子 Agent 工具面（start / check / cancel / send_message / list_tasks）从 SuperAgent 装配层收编为 **`NoesisSubagentMiddleware`**，与既有 middleware 栈同构；工具返回 `Command` 写入任务身份。
- **任务身份进 graph state**：`bg_tasks` 保存 task_id / child_session_id / subagent_type / description 与状态快照，随 checkpoint 持久化、免疫上下文压缩；state 定位为投影，权威状态永远实时取自执行器与 DB。
- child session 落库新增**版本化 subagent descriptor**（显式字段，独立于自由扩展的 extra 杂项键：type 与生效模型），供进程重启后任何重建 worker 的路径按类型取对配方。
- 执行器改名 `BackgroundTaskExecutor`：澄清它是承载 subagent / shell 双 kind 的后台任务运行时；执行内核（状态机、协作停止、终态收口）不动。
- `BackgroundTask` 增加 `subagent_type` 字段，透传任务卡投影与前端。

### 非目标

- 文件式 / 用户自定义角色定义（另立项，descriptor 为其预留位）。
- 增加第二个真实子 Agent 种类（本变更合入后另行验证分发）。
- 拆分执行器或改动其状态机、锁纪律、终态收口不变量。
- shell 任务能力变化（仅受益于改名，行为不变）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-background-tasks`：`start_task` 新增必填 `subagent_type` 参数与类型校验语义；task-worker 编译契约由单一配方改为按注册表分发；launch 落库新增 subagent descriptor；执行模型 requirement 中执行器命名与任务身份持久化语义更新（graph state 投影）。

## Impact

- 后端 `noesis-core`：`agents/subagents/`（tools 收编入新 middleware、executor 改名、`BackgroundTask` 加字段）、`agents/super_agent.py`（装配层退位为注册表构建）、`services/subagent_session_service.py`（launch 写 descriptor）、`agents/middlewares/stack.py`（挂载新 middleware）。
- 无 HTTP API / SSE 事件面破坏：`start_task` 等是模型侧工具而非对外接口；SSE 复用既有 task 事件，任务卡投影多一个 `subagent_type` 字段（前端可忽略）。
- 前端（可选收尾）：任务卡显示类型标识。
- 不做历史兼容：`subagent_type` 自本变更起为必填参数；历史 child session 无 descriptor 不回填（无消费路径，不做数据迁移）。
