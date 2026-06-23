## MODIFIED Requirements

### Requirement: create_noesis_agent 装配深度研究能力栈

`DeepResearchAgent` SHALL 通过 `create_noesis_agent` 创建 LangGraph Agent，且 **SHALL NOT** 在本场景单独调用裸 `create_agent` 绕过统一中间件顺序。装配 SHALL 满足：

- `backend`：由 `_build_research_backend()` 提供的 `CompositeBackend`；
- `subagents`：至少包含名为 `research-worker` 的同步子 Agent 规格；
- `extra_middleware`：主 Agent 挂载 `SkillsMiddleware(backend=backend, sources=["/skills/"])`；
- `tools`：`build_web_search_tools()` 的返回值（固定含 `web_search` 与 `web_fetch`）；
- `checkpointer`：继承 `BaseAgent` 的 `InMemorySaver`；
- 运行时防护栈：经 `build_noesis_runtime_middleware()` 挂载（含可选 `SummarizationOffloadMiddleware`、`ContextEditingMiddleware`、`LoopDetectionMiddleware`、`ToolCallLimitMiddleware` 等，以 `ModelConfig` 为准）。

文件系统工具仍由 `FilesystemMiddleware` 注入，**SHALL NOT** 与 `web_search` / `web_fetch` 重复注册。

中间件顺序 SHALL 与 `agent/factory.py` 文档一致：`FilesystemMiddleware` → `SubAgentMiddleware` → `extra_middleware`（Skills）→ 运行时防护 → `ToolCallLimitMiddleware`（尾栈）。

#### Scenario: 主 Agent 具备 task 委派能力

- **WHEN** `DeepResearchAgent` 完成 `create_noesis_agent` 初始化
- **THEN** 主 Agent SHALL 暴露 `task` 工具（由 `SubAgentMiddleware` 提供），且可接受 `subagent_type=research-worker`

#### Scenario: 子 Agent 默认中间件

- **WHEN** 系统构建 `research-worker` 子 Agent 中间件栈
- **THEN** SHALL 包含 `FilesystemMiddleware(backend=backend)` 与 `build_subagent_default_middleware` 中的运行时防护，并追加 `SkillsMiddleware(sources=["/skills/"])`

#### Scenario: 主 Agent 可搜索

- **WHEN** `DeepResearchAgent` 完成初始化
- **THEN** 主 Agent 工具集 SHALL 包含 `web_search` 与 `web_fetch`

#### Scenario: research-worker 继承 Web 工具

- **WHEN** 主 Agent 挂载了 Web 工具
- **THEN** `research-worker` 子 Agent 的 `tools` SHALL 包含与主 Agent 相同的 Web 工具列表，**SHALL NOT** 保持 `tools: []`

### Requirement: SkillsMiddleware 与 Available Skills 行为

主 Agent 与子 Agent（`research-worker`）SHALL 挂载 `SkillsMiddleware`，`sources` 均为 `["/skills/"]`。系统提示词 SHALL 要求 Agent：

- 执行研究前阅读 **Available Skills** 中与任务相关的 skill（优先 `deep-research-v2`）并按其协议执行；
- Phase 2 多源检索时，行业/竞品/政策类来源 **SHALL** 优先使用 `web_search` 发现 URL，再使用 `web_fetch` 或 `/skills/baoyu-url-to-markdown` 获取正文；
- 学术检索 **MAY** 继续使用 `execute` + OpenAlex API；
- 简单、一两步可完成的工作 SHALL 由主 Agent 直接完成，**SHALL NOT** 滥用 `task` 委派；
- 可拆分、需大量检索/读文件或宜并行的课题 SHALL 使用 `task` 工具委派 `research-worker`，并在 `description` 中写清目标与期望输出格式。

`research-worker` 子 Agent SHALL 在独立上下文中完成单课题调研，最终回复 SHALL 包含：关键发现、依据来源、未决问题与建议下一步；主 Agent **仅能**看到子 Agent 最终回复。

#### Scenario: 主 Agent 按 skill 与 Web 工具调研

- **WHEN** 用户提出需多步检索与归纳的深度研究问题，且 Web 工具已启用
- **THEN** 主 Agent SHALL 可先读取 `/skills/deep-research-v2/`，再调用 `web_search` 获取来源列表

#### Scenario: 子 Agent 返回结构化小结

- **WHEN** `research-worker` 完成一次 `task` 委派
- **THEN** 输出 SHALL 为面向主 Agent 汇总的结构化小结，中间工具步骤 **SHALL NOT** 作为独立顶层助手文本泄漏给前端（由 SSE 桥接 `parentTaskCallId` 规则约束，见 `platform-chat`）
