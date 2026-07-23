# agent-runtime-harness Specification

## Purpose

本能力规定 Noesis **Agent 运行时（Runtime）** 与 **Harness 平台层** 的职责边界、统一执行入口与依赖方向。Runtime 回答「如何构建并执行 LangGraph Agent」；Harness 回答「如何将 run 呈现给用户、评测与持久化」。对标 deer-flow harness/runtime 与 Yuxi `BaseContext` + `agent_run_service` 分层，支撑线上聊天、离线评测与嵌入式脚本共用同一执行路径。

## ADDED Requirements

### Requirement: Runtime 与 Harness 依赖方向

`backend/noesis_runtime/`（或后续等价包）SHALL 作为 Agent 运行时唯一权威模块，包含 factory、middlewares、backends、executor、profile 注册与嵌入式 client。Runtime **SHALL NOT** import `services`、`api`、`domain/chat` 模块。Harness 层（`services/`、`domain/chat/streaming/`、`evals/`）**MAY** import Runtime。

#### Scenario: Runtime 模块不依赖产品层

- **WHEN** 静态检查或代码审查 `noesis_runtime` 包 import 图
- **THEN** **SHALL NOT** 存在自 `noesis_runtime` 至 `services`、`api`、`domain.chat` 的 import 边

#### Scenario: Harness 调用 Runtime

- **WHEN** `QaService` 或 `evals.agent` 需要执行 Agent
- **THEN** **SHALL** 经 `AgentRunService` 或 `NoesisRuntimeClient` 进入 Runtime，**SHALL NOT** 在 Harness 层直接拼装 `create_agent` middleware 栈

### Requirement: AgentRuntimeContext 统一运行配置

系统 SHALL 提供 `AgentRuntimeContext`（或等价 dataclass），在一次 run 内承载 `qa_type`、`thread_id`、`user_id`、`model_id`、`query`、`kb_collections`、`file_list`、sandbox 策略及 Langfuse 关联字段。Harness **SHALL** 通过 `prepare_runtime_context()`（或等价函数）从 HTTP 请求、会话 `extra` 与 DB 解析并填充 Context；Runtime Profile **SHALL** 仅读取 Context 构建 graph，**SHALL NOT** 在 Profile 内访问数据库。

#### Scenario: 会话 KB 范围注入 Context

- **WHEN** 用户发起 `COMMON_QA` 且会话 `extra.kb_collections` 非空
- **THEN** `prepare_runtime_context()` **SHALL** 将规范化后的集合列表写入 `AgentRuntimeContext.kb_collections`
- **AND** `CommonQAProfile` **SHALL** 据此装配 RAG 工具范围

#### Scenario: 评测 Context 无 DB 降级

- **WHEN** `evals.agent` 在隔离环境执行且不提供 MySQL 会话
- **THEN** Harness **SHALL** 提供 `prepare_context_for_eval()`（或等价）填充最小 Context（含 `thread_id`、eval `user_id`）
- **AND** Runtime **SHALL** 可正常 `build_agent` 并执行

### Requirement: AgentRunService 统一执行入口

系统 SHALL 提供 `AgentRunService`（Harness 层）与 `AgentRunExecutor`（Runtime 层）分工：`AgentRunService` 负责创建 run、绑定 `MemoryStreamBridge`（或后续 event store）、调度后台 asyncio Task、暴露 `cancel_run(thread_id)`；`AgentRunExecutor` 负责消费 `agent.astream_events`、发出 LangGraph 原始事件及 `__tw_finish__` / `__tw_error__` / `__tw_abort__` 控制哨兵。

#### Scenario: 聊天流经 RunService 启动

- **WHEN** `QaService.exec_query` 处理允许的 `qa_type`（`COMMON_QA`、`SUPER_AGENT_QA`、`FAULT_OPERATION_QA`）
- **THEN** **SHALL** 调用 `AgentRunService.start_run(context)` 而非直接实例化 `GeneralQAAgent` / `SuperAgent` / `FaultOperationAgent` 并调用其 `run_agent`
- **AND** SSE 桥接 **SHALL** 订阅该 run 的事件流

#### Scenario: 用户停止 run

- **WHEN** 客户端调用 stop 且 `thread_id` 对应 run 正在执行
- **THEN** `AgentRunService.cancel_run(thread_id)` **SHALL** 标记取消
- **AND** `AgentRunExecutor` **SHALL** 在下一轮 `astream_events` 迭代前发出 `__tw_abort__` 并结束

### Requirement: Agent Profile 按 qa_type 装配

Runtime SHALL 维护 `AgentProfile` 注册表（`resolve_profile(qa_type)`），将 prompt、tools、`extra_middleware`、`backend`、`subagents` 装配逻辑从原 Agent 类收敛至 `noesis_runtime/profiles/`。`create_noesis_agent`（或迁入后的 factory）**SHALL** 仍作为底层 LangGraph 创建入口。

#### Scenario: SUPER_AGENT Profile 挂载沙箱 backend

- **WHEN** `qa_type` 为 `SUPER_AGENT_QA` 且 Context 中 sandbox 启用
- **THEN** `SuperAgentProfile.build_agent(context)` **SHALL** 调用 `create_agent_backend(user_id, session_id)` 并挂载 Filesystem、Memory、SubAgent 相关 middleware

#### Scenario: 未知 qa_type 拒绝

- **WHEN** `resolve_profile` 收到未注册的 `qa_type`
- **THEN** Runtime **SHALL** 在 `start_run` 前抛出明确错误，**SHALL NOT** 回退默认 Agent

### Requirement: NoesisRuntimeClient 嵌入式入口

Runtime SHALL 提供 `NoesisRuntimeClient`（或等价），允许脚本、Harbor worker、单测在不启动 FastAPI 的情况下调用与线上相同的 `build_agent` + `AgentRunExecutor` 路径。Client **SHALL** 支持注入 `checkpointer`（评测可用 `MemorySaver`）与可选 `backend` 覆盖（如 Harbor `ProxyHarborBackend`）。

#### Scenario: Harbor worker 使用 Client

- **WHEN** `evals.agent.harbor.noesis_worker` 在子进程内执行 instruction
- **THEN** **SHALL** 通过 `NoesisRuntimeClient` 启动 run
- **AND** **SHALL NOT** 在 worker 内直接调用 `create_noesis_agent` 并手写 `astream_events` 循环

#### Scenario: BrowseComp 与线上共用 Profile

- **WHEN** `evals.agent._agent.run_super_agent` 执行深度研究类任务
- **THEN** **SHALL** 构造 `AgentRuntimeContext(qa_type=SUPER_AGENT_QA, ...)`
- **AND** **SHALL** 经 `NoesisRuntimeClient` 执行，与 `QaService` 使用同一 `SuperAgentProfile`

### Requirement: 过渡期兼容 re-export

迁移期间，`backend/agent/factory.py`、`backend/agent/base/base_agent.py` **MAY** 保留薄 re-export 或委托至 `noesis_runtime`，以保证外部 import 路径不立即破坏。兼容层 **SHALL** 在模块 docstring 或注释标明 deprecated 及目标 import 路径。

#### Scenario: 旧 import 仍可用

- **WHEN** 测试或扩展代码 `from agent.factory import create_noesis_agent`
- **THEN** 导入 **SHALL** 成功并指向 Runtime 实现
- **AND** 行为与迁移前一致
