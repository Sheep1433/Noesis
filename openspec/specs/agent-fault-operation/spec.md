## Purpose

本能力规定 Noesis **故障运维**（`FAULT_OPERATION_QA`）场景的 Agent 验收标准：`FaultOperationAgent` 通过 `create_noesis_agent` 工厂组装 LangChain Agent，经 `MultiServerMCPClient` 连接 MCP 运维工具（远程 SSH 诊断：read/grep/glob/bash 及场景工具），在本地沙箱工作区存放排查笔记，并支持 `task` 委派 `general-purpose` 子 Agent 并行多步排查。

会话生命周期、SSE 帧契约、消息持久化与停止生成等平台行为以 `openspec/specs/platform-chat/spec.md` 为单一事实来源；本规格仅描述故障运维 Agent 域内行为。

## Non-Goals

以下能力**未实现**，**不属于**本基线规格的 SHALL 范围；未来实现见 pending change `openspec/changes/fault-operation-agent-experience-learning/`（规格：`specs/fault-operation-experience-learning/spec.md`）：

- 运维经验采集、生命周期治理（`draft`/`active`/`disabled`）
- 故障对话结束后的经验写入准入与显式「可沉淀」信号
- 主推理前的历史经验检索注入与 Top-K 上下文合并
- `experience_learning_enabled` 等经验学习配置开关

当前 `FaultOperationAgent` SHALL 仅依赖静态系统提示、MCP 工具链与 `create_noesis_agent` 运行时中间件完成排查，不得假设存在经验库或检索增强。
## Requirements
### Requirement: FAULT_OPERATION_QA 路由至 FaultOperationAgent

当流式或非流式问答请求的 `qa_type` 为 `FAULT_OPERATION_QA` 时，系统 SHALL 在 `QAService` 编排层调用 `FaultOperationAgent.run_agent`，并将 `session_id`（或等价 `chat_id`）作为 LangGraph `thread_id` 传入；会话 `extra` 中记录的 `qa_type` SHALL 与请求一致。

停止生成（`POST /api/chat/sessions/{session_id}/stop`）时，若会话 `qa_type` 为 `FAULT_OPERATION_QA`，系统 SHALL 调用 `FaultOperationAgent.cancel_task` 标记对应 `thread_id` 任务取消。

本 Requirement **不**重复规定 SSE 事件类型、消息落库或 `LangGraphSseBridge` 行为；上述 SHALL 遵循 `platform-chat` 规格。

#### Scenario: 流式故障运维请求

- **WHEN** 客户端对 `POST /api/chat/sessions/stream` 提交合法载荷且 `qa_type` 为 `FAULT_OPERATION_QA`
- **THEN** 系统 SHALL 使用 `FaultOperationAgent` 作为上游 Agent 生成器，且不得路由至 `GeneralQAAgent`、`CaseCoordinator` 或 `DeepResearchAgent`

#### Scenario: 用户停止故障运维流

- **WHEN** 客户端在 `FAULT_OPERATION_QA` 会话的流式任务进行中调用 stop 接口
- **THEN** 系统 SHALL 通过 `FaultOperationAgent.cancel_task` 中止该 `session_id` 对应任务，并遵循平台 stop 的 SSE/落库语义

### Requirement: MCP 工具连接与工具集边界

`FaultOperationAgent` SHALL 通过 `MultiServerMCPClient` 以 `streamable_http` 传输连接配置的 MCP 端点（默认 `fault_ops` 服务），在每次 `run_agent` 调用时动态加载工具列表并注入 `create_noesis_agent(tools=...)`。

MCP 工具集 SHALL 至少覆盖以下语义分层（具体工具名以实现注册为准）：

| 层级 | 工具 | 行为边界 |
|------|------|----------|
| L1 配置 | `setup_passwordless_login` | 一次性密码引导，将本机公钥写入远程 `authorized_keys` 并验证免密登录 |
| L1 原子 | `read` | 经 SSH 读取远程**文本文件**（绝对路径）；支持 `offset`/`limit` 行范围；拒绝二进制/图片 |
| L1 原子 | `grep` | 经 SSH 在远程路径正则搜索；支持 `output_mode`、`glob`、`ignore_case`、`head_limit` 等 |
| L1 原子 | `glob` | 经 SSH 按 glob 模式匹配远程文件列表（最多 100 个） |
| L1 原子 | `bash` | 经 SSH 执行 shell 命令；须携带 `ip`；`timeout_ms` 默认 120000；仅用于诊断类命令 |
| L2 场景 | `system_info` | 组合诊断命令输出主机资源概览（CPU/内存/磁盘/进程等） |
| L2 场景 | `playbook_log` | 检索并读取 Ansible playbook 日志，可过滤错误行 |

所有 MCP 工具调用 SHALL 要求显式 `ip`（目标主机）；Agent 系统提示 SHALL 规定：只使用提供的 MCP 工具操作远程环境，不编造命令或执行结果，日志分析须给出明确结论，修复建议须具体可操作。

#### Scenario: MCP 端点可达时加载工具

- **WHEN** `FaultOperationAgent.run_agent` 开始且 MCP 服务可用
- **THEN** 系统 SHALL 成功 `get_tools()` 并将非空工具列表传入 Agent，且工具调用经 SSE `tool-*` 帧对外可见（平台桥接规则见 `platform-chat`）

#### Scenario: read 行范围与截断

- **WHEN** Agent 调用 `read` 且指定 `offset` 与 `limit`
- **THEN** MCP 实现 SHALL 仅返回该范围内的远程文件内容（或约定 JSON 中的 `truncated` 标识），不得返回未请求路径的内容

#### Scenario: bash 超时

- **WHEN** Agent 调用 `bash` 且远程命令超过配置的 `timeout`
- **THEN** MCP 实现 SHALL 终止等待并返回可辨失败结果，Agent SHALL 在回复中如实反映失败而非伪造输出

### Requirement: create_noesis_agent 工厂组装

`FaultOperationAgent` SHALL 使用 `create_noesis_agent`，且：

- `backend`：`AioSandboxBackend` 经 `create_user_sandbox_backend(user_id, session_id)`；**用户级** AIO 容器；构建前 `ensure_workspace_dir`
- 其余：`tools`（MCP）、`system_prompt`、`checkpointer`、`subagents`（general-purpose）同现规格

#### Scenario: 本地工作区隔离

- **WHEN** Agent 读写排查笔记，且 `session_id` 与 `current_user` 有效
- **THEN** filesystem 默认操作 SHALL 落在当前 session workspace；**MAY** 与同用户其它 session **共用** 一个 AIO 容器

#### Scenario: 子 Agent 委派

- **WHEN** 主 Agent 调用 `task` 且 `subagent_type` 为 `general-purpose`
- **THEN** 子 Agent SHALL 在独立上下文中使用相同 MCP 工具；SSE 嵌套遵循 `platform-chat`

#### Scenario: 运行时防护生效

- **WHEN** `ModelConfig.tool_call_limit_enabled` 或 `ModelConfig.loop_detection_enabled` 为真
- **THEN** `FaultOperationAgent` SHALL 继承 `create_noesis_agent` 对应中间件

### Requirement: 远程执行与 MCP 安全约束（高层）

1. **远程 MCP（SSH）**：只读诊断；**SHALL NOT** 假设任意远程写。
2. **本地工作区**：**用户级** AIO 沙箱 + session virtual `/`；**SHALL NOT** 使用 `LocalShellBackend`；**SHALL NOT** 读取 API 环境或其它 **用户** workspace。

#### Scenario: Agent 不伪造远程结果

- **WHEN** MCP 工具返回失败或空结果
- **THEN** Agent 回复 SHALL 基于实际工具输出

#### Scenario: 只读诊断边界

- **WHEN** 用户要求变更类操作
- **THEN** Agent SHALL 给诊断与建议

#### Scenario: 本地沙箱不可读 API 环境

- **WHEN** 用户诱导 Agent `execute` 读取 `/app/.env`
- **THEN** **SHALL NOT** 返回 Noesis 业务密钥

#### Scenario: 同用户跨 session 排查笔记（未来）

- **WHEN** Agent 经 `execute` 读取同用户其它 session 的 workspace 文件
- **THEN** **MAY** 成功（同一 AIO 容器 mount）

### Requirement: 故障运维输出契约

`FaultOperationAgent` 的系统提示 SHALL 要求助手以**中文 Markdown** 回复，结构清晰，错误信息突出；复杂排查流程 SHOULD 按「现象 → 关键证据（日志/命令摘要）→ 根因判断 → 可操作建议」组织。

流式输出 SHALL 经 `BaseAgent._stream_agent_response` → `astream_events` → 平台 `LangGraphSseBridge` 对外暴露；本 Requirement **不**新增 SSE 事件类型。

#### Scenario: 正常完成回复

- **WHEN** 一次 `FAULT_OPERATION_QA` 流式回合正常结束
- **THEN** assistant 消息 SHALL 含可读 Markdown 正文（`text` part），且可含 MCP 工具调用的 `tool` parts；结束语义遵循平台 `finish` / `[DONE]`

#### Scenario: Agent 异常中止

- **WHEN** `FaultOperationAgent.run_agent` 捕获未处理异常或 `CancelledError`
- **THEN** 系统 SHALL 产出 `abort` 类型块或等价平台错误语义，并清理 `running_tasks` 中对应 `thread_id` 条目

