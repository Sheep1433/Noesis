# agent-profiles Specification

## Purpose

本能力规定四大 `qa_type` 对应的 Agent **产品行为与装配边界**：`COMMON_QA`、`SUPER_AGENT_QA`、`FAULT_OPERATION_QA`、`TEST_CASE_QA`。运行时路径/沙箱见 `agent-runtime`；HITL 策略见 `agent-hitl`；平台路由见 `platform-chat`。实现目录：`agent/profiles/`、`agent/case_generate/`。

## Requirements

### Requirement: COMMON_QA / GeneralQAAgent

`COMMON_QA` SHALL 使用 GeneralQAAgent：以知识库 RAG 工具为主（hybrid 检索链路见 `knowledge-base`），MAY 结合会话附件。**SHALL NOT** 默认挂载完整 SuperAgent Skills/子 Agent 栈。

#### Scenario: 路由到通用问答

- **WHEN** 流式请求 `qa_type=COMMON_QA`
- **THEN** 系统 SHALL 装配 GeneralQAAgent 而非 SuperAgent

### Requirement: SUPER_AGENT_QA / SuperAgent

`SUPER_AGENT_QA` SHALL 装配 SuperAgent：会话工作区、`/skills/public|personal`、`/memory/`、web 工具、可选 `task` 子 Agent、可选 HITL。提示词 SHALL 指引使用 `/workspace/...` 绝对路径，**SHALL NOT** 再教虚拟根 `/notes.md`。

工作区内研究产出约定目录为 `workspace/research/`（Agent 路径 `/workspace/research/...`），**SHALL NOT** 将其建模为独立 virtual root `/research/`。

#### Scenario: Skills 路径

- **WHEN** SuperAgent 读取平台 skill 文件
- **THEN** 路径 SHALL 形如 `/skills/public/{name}/SKILL.md`

#### Scenario: 研究笔记

- **WHEN** 模型写入研究报告
- **THEN** 目标 SHOULD 为 `/workspace/research/...` 下文件

### Requirement: FAULT_OPERATION_QA / FaultOperationAgent

`FAULT_OPERATION_QA` SHALL 使用 FaultOperationAgent，工具以 MCP（用户/平台合并配置，见 `user-platform`）为主，并 SHALL 使用与其它 Agent 相同的 `create_agent_backend` 会话工作区（docker 或 local_shell）。**SHALL NOT** 依赖已移除的 `AioSandboxBackend` 或「同用户跨 session 共用容器」模型。

#### Scenario: 工作区隔离

- **WHEN** 同一用户两个故障运维会话分别写入文件
- **THEN** 文件 SHALL 落在各自 `sessions/{sid}/workspace/`，互不覆盖

### Requirement: TEST_CASE_QA / CaseCoordinator

`TEST_CASE_QA` SHALL 使用 CaseCoordinator（LangGraph 多阶段 workflow）：需求理解、用例生成、可选评测阶段；阶段进度 SHALL 经 SSE / parts 可观测。知识库集合配置来自 PostgreSQL `kb_collection_config`（或现行表名）。

两阶段离线评测入口见 `offline-evals`，**SHALL NOT** 与在线 chat 路径混淆为同一进程职责。

#### Scenario: 阶段可观测

- **WHEN** 用例生成进入新 phase
- **THEN** 客户端 SHALL 能区分阶段（SSE 事件或 message parts）

### Requirement: 共享工厂

除 CaseCoordinator 外，场景 Agent SHALL 经 `create_noesis_agent`（或现行工厂）装配模型、中间件与 backend；**SHALL NOT** 在各 profile 内复制 divergent 的路径 canonicalize 逻辑。

#### Scenario: 统一 backend

- **WHEN** SuperAgent 与 FaultOperationAgent 在同一 sandbox 配置下创建
- **THEN** 二者 workspace/skills/memory 路由规则 SHALL 与 `agent-runtime` 一致
