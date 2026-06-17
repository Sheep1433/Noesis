## Purpose

本能力规定 Noesis 第 4 类问答场景 **深度研究（`DEEP_RESEARCH_QA`）** 的后端 Agent 实现：`DeepResearchAgent` 经统一工厂 `create_noesis_agent` 装配 deepagents 的 **FilesystemMiddleware**、**SkillsMiddleware** 与 **SubAgentMiddleware**；工作区写入 `.data/agent_workspace/users/{user_id}/sessions/{session_id}/workspace/`（见 `agent-workspace` 规格），Skills 以 `/skills/` 路由只读挂载 `extensions/skills`；主 Agent 可通过 `task` 工具委派 `research-worker` 子 Agent 并行调研，流式输出与前端子任务展示遵循平台聊天规格。

## Requirements

### Requirement: qa_type 路由至 DeepResearchAgent

系统 SHALL 在流式与非流式问答入口（`QAService` 等）根据请求体 `qa_type` 路由：当 `qa_type` 为 `DEEP_RESEARCH_QA` 时，SHALL 调用 `DeepResearchAgent.run_agent`，并将 `qa_type` 透传至流式桥接与持久化元数据；停止生成（`stop_chat`）SHALL 对同类型调用 `DeepResearchAgent.cancel_task`。

#### Scenario: 流式深度研究请求

- **WHEN** 已认证用户向 `POST /api/chat/sessions/stream` 提交 `qa_type=DEEP_RESEARCH_QA` 与非空用户问题
- **THEN** 系统 SHALL 实例化 `DeepResearchAgent` 流水线产出 SSE 事件流，且会话 `extra` 中记录 `DEEP_RESEARCH_QA`

#### Scenario: 未知 qa_type 拒绝

- **WHEN** 请求体 `qa_type` 不在 `IntentEnum` 已支持枚举内
- **THEN** 系统 SHALL 返回错误 SSE 帧或业务失败响应，**SHALL NOT** 静默落入 `DeepResearchAgent`

#### Scenario: 用户停止深度研究

- **WHEN** 用户对进行中的 `DEEP_RESEARCH_QA` 会话发起停止
- **THEN** 系统 SHALL 标记对应 `task_id` 取消并结束流式输出，行为与其它 `BaseAgent` 子类一致

### Requirement: create_noesis_agent 装配深度研究能力栈

`DeepResearchAgent` SHALL 通过 `create_noesis_agent` 创建 LangGraph Agent，且 **SHALL NOT** 在本场景单独调用裸 `create_agent` 绕过统一中间件顺序。装配 SHALL 满足：

- `backend`：由 `_build_research_backend()` 提供的 `CompositeBackend`；
- `subagents`：至少包含名为 `research-worker` 的同步子 Agent 规格；
- `extra_middleware`：主 Agent 挂载 `SkillsMiddleware(backend=backend, sources=["/skills/"])`；
- `tools`：空列表（文件系统工具由 `FilesystemMiddleware` 注入，**SHALL NOT** 重复注册）；
- `checkpointer`：继承 `BaseAgent` 的 `InMemorySaver`；
- 运行时防护栈：经 `build_noesis_runtime_middleware()` 挂载（含可选 `SummarizationOffloadMiddleware`、`ContextEditingMiddleware`、`LoopDetectionMiddleware`、`ToolCallLimitMiddleware` 等，以 `ModelConfig` 为准）。

中间件顺序 SHALL 与 `agent/factory.py` 文档一致：`FilesystemMiddleware` → `SubAgentMiddleware` → `extra_middleware`（Skills）→ 运行时防护 → `ToolCallLimitMiddleware`（尾栈）。

#### Scenario: 主 Agent 具备 task 委派能力

- **WHEN** `DeepResearchAgent` 完成 `create_noesis_agent` 初始化
- **THEN** 主 Agent SHALL 暴露 `task` 工具（由 `SubAgentMiddleware` 提供），且可接受 `subagent_type=research-worker`

#### Scenario: 子 Agent 默认中间件

- **WHEN** 系统构建 `research-worker` 子 Agent 中间件栈
- **THEN** SHALL 包含 `FilesystemMiddleware(backend=backend)` 与 `build_subagent_default_middleware` 中的运行时防护，并追加 `SkillsMiddleware(sources=["/skills/"])`

### Requirement: CompositeBackend 工作区与 Skills 路由

深度研究文件系统 SHALL 使用 deepagents `CompositeBackend`，满足：

- **默认盘**：`LocalShellBackend(root_dir={REPO_ROOT}/.data/agent_workspace/users/{user_id}/sessions/{session_id}/workspace/, virtual_mode=True)`，其中 `user_id` 来自 `current_user`，`session_id` 来自 `run_agent` 参数；目录 SHALL 在构建 backend 前通过 `ensure_workspace_dir` 创建；
- **Skills 路由**：路径前缀 `/skills/` 映射至 `LocalShellBackend(root_dir=extensions/skills（或 skills_filesystem_root 配置）, virtual_mode=True)`；
- Agent 侧对 `/skills/` 的访问语义为 **只读技能包挂载**（写入行为由 deepagents 后端与系统提示约束，**SHALL NOT** 依赖 HTTP 技能 API）。

系统 **SHALL NOT** 将遗留全局 `backend/.agent_workspace` 或 `.data/agent_workspace` 根目录本身（无 `users/.../sessions/...` 段）作为单次运行的可写 backend 根。

#### Scenario: 工作区路径写入

- **WHEN** 主 Agent 或子 Agent 通过文件系统工具在默认路径创建或修改文件，且 `run_agent` 传入 `session_id=s1` 与有效 `current_user`
- **THEN** 变更 SHALL 落在 `.data/agent_workspace/users/{user_id}/sessions/s1/workspace/` 虚拟根下，**SHALL NOT** 直接写入 `extensions/skills`，**SHALL NOT** 写入遗留 `backend/.agent_workspace`

#### Scenario: 读取 Skills 下 skill 目录

- **WHEN** Agent 通过文件系统工具访问以 `/skills/` 开头的路径（如 `/skills/deep-research-v2/SKILL.md`）
- **THEN** 系统 SHALL 从 `extensions/skills`（或配置覆盖路径）对应相对路径读取内容，供 `SkillsMiddleware` 与 Agent 提示词引用

#### Scenario: 无 backend 时不得挂载子 Agent

- **WHEN** 调用方向 `create_noesis_agent` 传入 `subagents` 但 `backend` 为 `None`
- **THEN** 工厂 SHALL 抛出 `ValueError`，**SHALL NOT** 创建缺文件系统的委派栈

### Requirement: SkillsMiddleware 与 Available Skills 行为

主 Agent 与子 Agent（`research-worker`）SHALL 挂载 `SkillsMiddleware`，`sources` 均为 `["/skills/"]`。系统提示词 SHALL 要求 Agent：

- 执行研究前阅读 **Available Skills** 中与任务相关的 skill（优先 `deep-research-v2`）并按其协议执行；
- 简单、一两步可完成的工作 SHALL 由主 Agent 直接完成，**SHALL NOT** 滥用 `task` 委派；
- 可拆分、需大量检索/读文件或宜并行的课题 SHALL 使用 `task` 工具委派 `research-worker`，并在 `description` 中写清目标与期望输出格式。

`research-worker` 子 Agent SHALL 在独立上下文中完成单课题调研，最终回复 SHALL 包含：关键发现、依据来源、未决问题与建议下一步；主 Agent **仅能**看到子 Agent 最终回复。

#### Scenario: 主 Agent 按 skill 指引调研

- **WHEN** 用户提出需多步检索与归纳的深度研究问题
- **THEN** 主 Agent SHALL 可先读取 `/skills/` 下相关 skill 内容，再决定自行执行或委派子任务

#### Scenario: 子 Agent 返回结构化小结

- **WHEN** `research-worker` 完成一次 `task` 委派
- **THEN** 输出 SHALL 为面向主 Agent 汇总的结构化小结，中间工具步骤 **SHALL NOT** 作为独立顶层助手文本泄漏给前端（由 SSE 桥接 `parentTaskCallId` 规则约束，见 `platform-chat`）

### Requirement: summary_offload 与 StateBackend 适用性

经 `create_noesis_agent` 挂载的 `SummarizationOffloadMiddleware`（`create_summary_offload_middleware`，受 `ModelConfig.summarization_enabled` 控制）SHALL 在深度研究场景按以下规则解析卸载后端：

- 优先使用运行时 `FilesystemMiddleware` 提供的 `backend`（即 `CompositeBackend`），超大 tool 结果 **SHALL** 卸载至工作区文件并以内联占位符替换消息内容；
- 仅当状态中含 `files` 且无法从 runtime 解析 backend 时，**MAY** 回退 `deepagents.backends.StateBackend`；
- 卸载失败时 **SHALL** 使用约定丢弃占位符，**SHALL NOT** 因卸载异常中断整轮对话。

本 Requirement 规定 Agent 运行时上下文治理；**SHALL NOT** 重复定义 `ModelConfig` 各阈值字段含义。

#### Scenario: 长 tool 输出卸载至工作区

- **WHEN** `summarization_enabled` 为真且某次文件系统 tool 返回 token 数超过 `summarization_tool_offload_threshold`
- **THEN** 中间件 SHALL 将完整内容写入 `CompositeBackend` 工作区路径，并在对话消息中保留可读的 offload 占位与预览

#### Scenario: 关闭 summarization 时不挂载

- **WHEN** `ModelConfig.summarization_enabled` 为假
- **THEN** `create_summary_offload_middleware` SHALL 返回 `None`，深度研究 Agent 栈 **SHALL NOT** 包含 `SummarizationOffloadMiddleware`

### Requirement: 与 skills-filesystem 规格的引用边界

本能力（Agent 运行时读盘）与 `openspec/specs/skills-filesystem/spec.md`（HTTP 磁盘技能管理）SHALL 职责分离：

| 维度 | `agent-deep-research`（本规格） | `skills-filesystem` |
|------|----------------------------------|---------------------|
| 消费方 | LangGraph Agent / deepagents 中间件 | 前端 Skills 管理页、运维上传 |
| 访问面 | `FilesystemMiddleware` + `SkillsMiddleware` 虚拟路径 | `GET/POST /api/skills/fs/*` REST |
| 写入范围 | 当前会话 `.data/agent_workspace/users/.../workspace/` | ZIP 上传解压至技能根目录 |
| 认证 | 继承聊天会话 JWT 链路的 Agent 进程内访问 | 接口级 JWT 校验 |

本规格 **SHALL NOT** 复制 `skills-filesystem` 中的 API 路径、响应字段或 ZIP 大小限制细节；当 Agent 需读取 skill 内容时，**SHALL** 通过 `/skills/` 路由与 `SkillsMiddleware` 完成，**SHALL NOT** 在 Agent 代码中直接调用 `/api/skills/fs/file`。

#### Scenario: 运维上传新 skill 后 Agent 可见

- **WHEN** 运维经 `skills-filesystem` 约定 API 向 `extensions/skills` 写入新 skill 包
- **THEN** 后续 `DEEP_RESEARCH_QA` 会话中 Agent **SHALL** 能在 `/skills/` 路径下发现该 skill，无需修改 `DeepResearchAgent` 代码

#### Scenario: Agent 规格不定义 HTTP 树接口

- **WHEN** 审查者仅阅读本 `agent-deep-research` 规格
- **THEN** 不得要求在本能力内实现 `GET /api/skills/fs/tree`；该行为 **SHALL** 引用 `skills-filesystem`

### Requirement: task 子任务与 SubagentCollapse 展示

深度研究主 Agent 使用 `task` 工具委派 `research-worker` 时，SSE 与前端展示 **SHALL** 遵循 `openspec/specs/platform-chat/spec.md` 中关于 `task` 工具与 `SubagentCollapse` 的 Requirements（含 `parentTaskCallId` 嵌套规则），包括但不限于：

- `toolName=task` 的 part **SHALL** 使用 `SubagentCollapse`，展示 `input.description` 为标题、`input.subagent_type`（期望值为 `research-worker`）为类型标签；
- 子 Agent 内部的文件系统 tool（如 `read`、`write`、`grep` 等由 `FilesystemMiddleware` 注入者）及 `text`/`reasoning` parts **SHALL** 带 `parentTaskCallId` 嵌套在对应 `SubagentCollapse` 内，**SHALL NOT** 在主聊天气泡顶层平铺；
- **SHALL NOT** 为深度研究单独新增 SSE 事件类型。

本 Requirement 仅声明深度研究场景 **适用** 平台聊天子 Agent 展示契约，UI 组件与解析函数细节 **SHALL** 以 `platform-chat` 为单一事实来源。

#### Scenario: 委派 research-worker 流式展示

- **WHEN** 深度研究会话 SSE 出现 `tool-input-available`，`toolName` 为 `task`，`input` 含 `{ "description": "调研 A 主题", "subagent_type": "research-worker" }`
- **THEN** chat 页 **SHALL** 渲染 `SubagentCollapse`，标题为「调研 A 主题」，状态随 output 更新

#### Scenario: 子 Agent 内部 read 嵌套展示

- **WHEN** `research-worker` 执行期间桥接层产出 `toolName=read`（或等价文件系统工具）且 `parentTaskCallId` 指向该次 `task` 的 `toolCallId`
- **THEN** 主界面顶层 **SHALL NOT** 出现独立 `ToolCallCollapse`；用户展开对应 `SubagentCollapse` **SHALL** 看到该工具块

#### Scenario: 主 Agent 顶层文件操作仍平铺

- **WHEN** 主 Agent 自身（非子 Agent 窗口）调用文件系统 tool 且 part **无** `parentTaskCallId`
- **THEN** 前端 **SHALL** 在主界面以 `ToolCallCollapse` 平铺展示，与故障运维等场景一致

### Requirement: 流式输出与异常语义

`DeepResearchAgent.run_agent` SHALL 通过 `BaseAgent._stream_agent_response` 消费 `agent.astream_events`，以 `stream_mode=messages` 产出 LangGraph 事件 dict，并 SHALL 将 `qa_type`、`langfuse_session_id`（若启用）传入 stream 配置。取消与未预期异常 SHALL yield 约定 `abort` 控制帧，由 `LangGraphSseBridge` 转换为前端可识别的结束语义。

#### Scenario: 用户取消流式生成

- **WHEN** 流式过程中触发 `asyncio.CancelledError`
- **THEN** Agent SHALL yield `type=abort` 且 `finish_reason=stop`，并清理 `running_tasks` 中的 `task_id`

#### Scenario: 运行时未捕获异常

- **WHEN** `run_agent` 发生未预期异常
- **THEN** Agent SHALL 记录 exception 日志、yield `type=abort` 且 `finish_reason=error`，并清理 `running_tasks`
