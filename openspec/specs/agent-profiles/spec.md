# agent-profiles Specification

## Purpose

本能力规定四大 `qa_type` 对应的 Agent **产品行为与装配边界**：`COMMON_QA`、`SUPER_AGENT_QA`、`FAULT_OPERATION_QA`、`TEST_CASE_QA`。运行时路径/沙箱见 `agent-runtime`；HITL 策略见 `agent-hitl`；平台路由见 `platform-chat`。实现目录：`packages/noesis-core/src/noesis/agents/`、`packages/noesis-core/src/noesis/agents/case_generate/`。
## Requirements
### Requirement: COMMON_QA / GeneralQAAgent

`COMMON_QA` SHALL 使用 GeneralQAAgent：以知识库 RAG 工具为主（hybrid 检索链路见 `knowledge-base`），MAY 结合会话附件。**SHALL NOT** 默认挂载完整 SuperAgent Skills/子 Agent 栈。回答 SHALL 保持普通 Markdown 文本输出和 token streaming。系统 SHALL NOT 为 citation 向 `create_agent` 传递 structured `response_format`，也 SHALL NOT 创建提交最终答案的虚拟 Tool。

当回答使用检索事实时，system prompt SHALL 要求 Web 和 KB 统一使用 `[n]`，并在文末 `### 参考资料` 按编号列出工具返回的精确来源字段。Web 条目 SHALL 包含原始 URL；KB 条目 SHALL 包含文件名、Collection 和可用 locator。Agent SHALL NOT 输出内部 evidence/document/segment ID，也 SHALL NOT 编造工具未提供的来源。

#### Scenario: Web 检索后正常流式回答

- **WHEN** GeneralQAAgent 使用 Web 结果回答事实问题
- **THEN** 答案 SHALL 通过普通 `text-delta` 流式输出
- **AND** 使用的来源 SHALL 以 `[n]` 出现在相应事实附近
- **AND** 文末对应条目 SHALL 包含原始 URL
- **AND** 系统 SHALL NOT 等待 structured response 才交付正文

#### Scenario: 路由到通用问答

- **WHEN** 流式请求 `qa_type=COMMON_QA`
- **THEN** 系统 SHALL 装配 GeneralQAAgent 而非 SuperAgent

#### Scenario: KB 检索后编号引用

- **WHEN** GeneralQAAgent 使用知识库片段回答
- **THEN** 正文 SHALL 使用简短编号引用
- **AND** 文末 SHALL 提供编号一致的 `### 参考资料`

### Requirement: SUPER_AGENT_QA / SuperAgent

`SUPER_AGENT_QA` SHALL 装配 SuperAgent：会话工作区、`/skills/public|personal`、`/memory/`、web 工具、后台子 Agent 工具面（`start_task` / `check_task` / `cancel_task` / `list_tasks` / `send_message`）、可选 HITL。提示词 SHALL 指引使用 `/workspace/...` 绝对路径，**SHALL NOT** 再教虚拟根 `/notes.md`。SuperAgent 栈 SHALL NOT 挂同步 `SubAgentMiddleware` 的 `task` 工具（同步委派已退役）。

提示词委派语义 SHALL 为单工具按依赖选同异步：独立可并行的子任务**一起 `start_task`（默认后台）后继续当前工作或直接回复用户**；**下一步动作依赖该结果**时用 `run_in_background=false` 前台等待（超过约 2 分钟自动转后台）。收到 `[系统通知]` 后用 `check_task` 收取结果；**SHALL NOT** 指引启动后立即反复 check；追加指示或后续工作用 `send_message`（向该子 Agent 会话追加一个新 turn）；需在主上下文连续推理且不可隔离的链 SHALL 留在主上下文。

工作区内研究产出约定目录为 `workspace/research/`（Agent 路径 `/workspace/research/...`），**SHALL NOT** 将其建模为独立 virtual root `/research/`。

SuperAgent 主 Agent SHALL 使用与 COMMON_QA 相同的普通 Markdown citation 规则。子 Agent 返回给主 Agent 的研究小结 SHOULD 保留原始来源 URL。系统 SHALL NOT 为不同模型维护 citation provider allowlist。

#### Scenario: Skills 路径

- **WHEN** SuperAgent 读取平台 skill 文件
- **THEN** 路径 SHALL 形如 `/skills/public/{name}/SKILL.md`

#### Scenario: 研究笔记

- **WHEN** 模型写入研究报告
- **THEN** 目标 SHOULD 为 `/workspace/research/...` 下文件

#### Scenario: 异步委派不阻塞

- **WHEN** 主 Agent 以默认参数调用 `start_task` 委派重子任务
- **THEN** 本轮 SHALL 继续执行后续步骤或直接回复用户，不等待子任务

#### Scenario: 依赖结果选前台

- **WHEN** 主 Agent 的下一步动作依赖子任务结果
- **THEN** 提示词 SHALL 指引用 `run_in_background=false`，工具返回终态文本

#### Scenario: 通知驱动的收果

- **WHEN** 模型输入包含 `[系统通知] 后台任务 … 已完成`
- **THEN** 模型 SHALL 用 `check_task` 收取结果并汇总给用户

#### Scenario: 主 Agent 汇总 Web 调研结果

- **WHEN** SuperAgent 使用主 Agent 或子 Agent 返回的 Web 来源生成最终报告
- **THEN** 最终回答 SHALL 保持普通文本流式输出，不启用 citation structured response

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

