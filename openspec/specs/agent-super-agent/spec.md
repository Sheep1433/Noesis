# agent-super-agent Specification

## Purpose

本能力规定 Noesis **通用超级智能体**（`SUPER_AGENT_QA`）的后端实现：`SuperAgent` 经 `create_noesis_agent` 装配文件系统、Skills、子 Agent 委派、Web 检索与用户记忆中间件；工作区为 session 级 `/research/`；专项流程（如深度调研）由 Skills 触发而非硬编码 prompt workflow；流式与前端展示遵循 `platform-chat`。
## Requirements
### Requirement: qa_type 路由至 SuperAgent

系统 SHALL 在流式与非流式问答入口根据请求体 `qa_type` 路由：当 `qa_type` 为 `SUPER_AGENT_QA` 时，SHALL 调用 `SuperAgent.run_agent`，并将 `qa_type` 透传至流式桥接与持久化元数据；停止生成（`stop_chat`）SHALL 对同类型调用 `SuperAgent.cancel_task`。

系统 **SHALL NOT** 注册 `DEEP_RESEARCH_QA` 或 `DeepResearchAgent` 作为有效路由目标。

#### Scenario: 流式超级智能体请求

- **WHEN** 已认证用户向 `POST /api/chat/sessions/stream` 提交 `qa_type=SUPER_AGENT_QA` 与非空用户问题
- **THEN** 系统 SHALL 实例化 `SuperAgent` 流水线产出 SSE 事件流，且会话 `extra` 中记录 `SUPER_AGENT_QA`

#### Scenario: 拒绝已废弃的 DEEP_RESEARCH_QA

- **WHEN** 请求体 `qa_type=DEEP_RESEARCH_QA`
- **THEN** 系统 SHALL 返回业务错误（与未知 qa_type 同等处理），**SHALL NOT** 静默路由至 SuperAgent

#### Scenario: 用户停止超级智能体会话

- **WHEN** 用户对进行中的 `SUPER_AGENT_QA` 会话发起停止
- **THEN** 系统 SHALL 标记对应 `task_id` 取消并结束流式输出

### Requirement: create_noesis_agent 装配超级智能体能力栈

`SuperAgent` SHALL 通过 `create_noesis_agent` 创建 LangGraph Agent。装配 SHALL 满足：

- `backend`：`create_agent_backend(user_id, session_id)` 提供的 `CompositeBackend`（含 `/research/`、`/memory/`、`/skills/`）；
- `system_prompt`：`build_prompt(PromptProfile.SUPER_AGENT, user_id=...)`；
- `subagents`：至少包含名为 `task-worker` 的同步子 Agent；
- `extra_middleware`（顺序在 `SubAgentMiddleware` 之后、运行时防护之前）：`TodoListMiddleware`、`SkillsMiddleware`、`MemoryMiddleware`（见 `agent-user-memory`）；
- `tools`：`build_web_search_tools()` 返回的 Web 工具列表（由 Agent 显式挂载，**SHALL NOT** 与 Filesystem 工具重复注册）；
- 运行时防护：`build_noesis_runtime_middleware()`（含 `SessionClockMiddleware`、`SummarizationOffloadMiddleware` 等，以 `ModelConfig` 为准）。

`task-worker` 子 Agent **SHALL NOT** 挂载 `MemoryMiddleware`。

#### Scenario: 主 Agent 具备 task 委派能力

- **WHEN** `SuperAgent` 完成 `create_noesis_agent` 初始化
- **THEN** 主 Agent SHALL 暴露 `task` 工具，且可接受 `subagent_type=task-worker`

#### Scenario: 子 Agent 不加载用户记忆

- **WHEN** 系统构建 `task-worker` 中间件栈
- **THEN** SHALL 包含 `FilesystemMiddleware` 与 `SkillsMiddleware`，**SHALL NOT** 包含 `MemoryMiddleware`

### Requirement: 超级智能体 system prompt 结构

系统 prompt SHALL 由 `agent/prompts/super_agent.py` 组装，并 **SHALL** 包含下列逻辑块（允许措辞迭代，语义不得缺失）：

- 通用身份与 `/research/`、`/skills/` 路径说明；
- 交互分流（寒暄/无任务时禁止调用工具）；
- 执行纪律（任务完成标准、工具执行纪律、并行工具调用、操作规范）；
- 编排原则（先匹配 Skill、`write_todos`、并行 `task-worker`、质量把关）；
- Skills 索引块（`skills_index` 动态生成）；
- **SHALL NOT** 内嵌与 `deep-research-v2` Skill 重复的完整六阶段 workflow 表。

子 Agent prompt SHALL 使用 `PromptProfile.SUPER_AGENT_SUB`，聚焦单个子任务执行与结构化小结交付。

#### Scenario: Prompt 含执行纪律与 Skills 索引

- **WHEN** 调用 `build_prompt(PromptProfile.SUPER_AGENT)`
- **THEN** 返回字符串 SHALL 包含 `<task_completion>`、`<tool_use_enforcement>`、`<skills_index>` 与 `task-worker`

### Requirement: Skills 与 Web 工具行为

主 Agent 与 `task-worker` SHALL 挂载 `SkillsMiddleware`，sources 为 `agent.backends.factory.SKILL_SOURCES`。系统 SHALL 要求 Agent：

- 回复前扫描 prompt 内 Skills 索引；任务匹配时先 `read_file` 对应 `SKILL.md`；
- 深度调研类任务 **SHALL** 优先加载 `deep-research-v2` 并按其协议在 `/research/<slug>/` 落盘；
- 互联网检索使用 `web_search` → `web_fetch` 或匹配的正文提取 Skill；
- 简单一两步任务由主 Agent 直接完成；可并行、上下文重的子课题 **SHALL** 使用 `task` 委派 `task-worker`。

#### Scenario: 调研任务触发 deep-research-v2

- **WHEN** 用户提出多步市场调研类问题
- **THEN** 主 Agent SHALL 可先读取 `/skills/extensions/deep-research-v2/SKILL.md` 再执行检索

#### Scenario: task-worker 返回结构化小结

- **WHEN** `task-worker` 完成一次 `task` 委派
- **THEN** 最终回复 SHALL 为面向主 Agent 的结构化 Markdown 小结，含关键发现、来源、已写文件路径与建议下一步

### Requirement: session 与 backend 绑定

`SuperAgent.run_agent` SHALL 要求有效 `session_id` 与 `user_id`；缺失时 **SHALL** 拒绝挂载可写 backend 并产出错误结束帧。

#### Scenario: 缺少 session_id 拒绝运行

- **WHEN** `run_agent` 未收到 `session_id` 或 `user_id`
- **THEN** SHALL 产出 `finish_reason=error` 的 abort 事件，**SHALL NOT** 创建无隔离 backend

### Requirement: SSE 与子 Agent 展示

`SuperAgent` 使用 `task` 委派 `task-worker` 时，SSE 与前端 **SHALL** 遵循 `platform-chat` 中 `SubagentCollapse` 与 `parentTaskCallId` 规则；`input.subagent_type` 期望值 **SHALL** 为 `task-worker`。

#### Scenario: 委派 task-worker 流式展示

- **WHEN** SSE 出现 `tool-input-available`，`toolName` 为 `task`，`input` 含 `{ "description": "...", "subagent_type": "task-worker" }`
- **THEN** 前端 SHALL 以 `SubagentCollapse` 展示子任务

### Requirement: SuperAgent SHALL 消费本轮 mentions

当 `qa_type` 为 `SUPER_AGENT_QA` 且请求含通过校验的 `mentions` 时，`SuperAgent` 运行前编排 SHALL：

- 对每个 `type=skill`：确保本轮 Skills 可见集包含该 skill，并在提示中标明用户点名该 skill（引导先读对应 `SKILL.md`）；
- 对每个 `type=file` / `folder`：注入可映射的 Agent 虚拟路径引用（`/research/...` 或规格允许的 uploads 映射），要求优先工具读取；
- 对每个 `type=subagent`：注入委派提示，优先考虑使用 `task` 且 `subagent_type` 为该 id（当前基线为 `task-worker`）；**SHALL NOT** 绕过既有「默认不随意委派」的安全边界去强制自动调用 tool，仅作强提示。

无 `mentions` 时行为与既有 SuperAgent 规格一致。

#### Scenario: skill mention 引导读取

- **WHEN** 用户提交 `SUPER_AGENT_QA` 且 `mentions` 含 `skill` id `deep-research-v2`
- **THEN** 本轮 Agent 上下文 SHALL 能识别该 skill 被用户点名，且该 skill 对本轮 SkillsMiddleware 可见

#### Scenario: subagent mention 提示委派类型

- **WHEN** 用户提交含 `subagent` id `task-worker` 的 mentions
- **THEN** 注入提示 SHALL 提及可委派 `task-worker`，且 **SHALL NOT** 在无用户任务需要时自动强制发起 `task` 调用

