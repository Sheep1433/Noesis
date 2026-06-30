## Why

深度研究场景（`DEEP_RESEARCH_QA`）的 Agent 实现与 prompt 长期绑定在「调研负责人 + research-worker + 六阶段 workflow」上，与已落地的通用 `super_agent` prompt、Skills 渐进披露方向冲突；同时缺少跨会话用户记忆层，无法让 Agent 持久化偏好与惯例。deepagents `>=0.6.7` 已提供 `MemoryMiddleware`（基于 AGENTS.md 规范），适合作为用户级 context 注入与可写记忆的标准挂载点。

本变更将 **深度研究升格为通用超级智能体**（调研仅为 Skill 触发的一种任务形态），并引入 **用户级 `/memory/` 路由 + MemoryMiddleware**，按最佳实践重构命名与规格，**不保留** `DeepResearchAgent` / `research-worker` / `DEEP_RESEARCH` prompt profile 等历史别名。

## What Changes

- **BREAKING**：`DeepResearchAgent` 重命名为 `SuperAgent`；`DEEP_RESEARCH_QA` 重命名为 `SUPER_AGENT_QA`（前后端、`IntentEnum`、评测入口、Locust 脚本一并更新）。
- **BREAKING**：子 Agent `research-worker` 重命名为 `task-worker`；`PromptProfile.DEEP_RESEARCH*` 删除，仅保留 `SUPER_AGENT` / `SUPER_AGENT_SUB`。
- **BREAKING**：删除 `agent/prompts/deep_research.py`；`agent-deep-research` 主规格由新能力 `agent-super-agent` 取代（归档时移除旧 spec）。
- 新增用户级记忆路径：`.data/users/{user_id}/AGENTS.md`、`.data/users/{user_id}/USER.md`；Agent 虚拟路由 `/memory/AGENTS.md`、`/memory/USER.md`（**不**放在 session workspace）。
- 主 `SuperAgent` 挂载 deepagents `MemoryMiddleware`（中文 guidelines、双 source）；子 `task-worker` **不**挂载。
- `ensure_user_root` 首次创建时 seed `AGENTS.md` 空模板；**不**注入仓库根 `AGENTS.md`（开发者文档）。
- `agent-runtime-paths` 扩展路径 API；`CompositeBackend` 增加 `/memory/` 可写路由（用户级，跨会话）。
- 平台规则继续由 Python stable prompt + `extensions/skills` 承担，不经 MemoryMiddleware。

## Capabilities

### New Capabilities

- `agent-super-agent`：通用超级智能体（原深度研究场景的统一实现）：`SuperAgent`、`SUPER_AGENT_QA` 路由、`task-worker` 子 Agent、`super_agent` prompt 与 Skills 索引、Web 工具与沙箱装配。
- `agent-user-memory`：用户级 AGENTS.md / USER.md 磁盘布局、Agent `/memory/` 虚拟路径、`MemoryMiddleware` 装配与写入边界、首启 seed。

### Modified Capabilities

- `agent-runtime-paths`：用户数据根下新增 `AGENTS.md`、`USER.md`；明确与 session workspace 职责边界；删 session **不**删除用户记忆。
- `platform-chat`：`qa_type` 枚举与 UI 标签 `DEEP_RESEARCH_QA` → `SUPER_AGENT_QA`；SSE/落库 `extra.qa_type` 对齐。
- `agent-sandbox`：AIO 挂载下 `/memory/` 对应宿主机 `users/{uid}/` 根文件可写（与 session workspace 分离）。

### Removed Capabilities（归档时）

- `agent-deep-research`：由 `agent-super-agent` 完全替代，不保留并行规格。

## Impact

| 区域 | 影响 |
|------|------|
| Agent | `deep_research_agent.py` → `super_agent.py`；`factory` / `qa_service` / `agent_lifecycle` |
| Prompt | 删除 `deep_research.py`；`__init__.py` 移除 `DEEP_RESEARCH*` profile |
| Backend FS | `agent_filesystem.py` 新增 `/memory/` 路由；`mount_paths.py` 常量 |
| 路径 | `user_data_paths.py`：`get_user_agents_md_path`、`ensure_user_memory_files` |
| 中间件 | `MemoryMiddleware` + 中文 `NOESIS_MEMORY_SYSTEM_PROMPT` |
| 常量 | `constants/code_enum.py`：`SUPER_AGENT_QA` |
| 前端 | `chat.vue`、`DefaultPage.vue`、`theme.ts`、`QatypeIcon` 等 qa_type 与文案 |
| 评测 | `evals/agent/_agent.py`、`wildclaw`、`browsecomp`、`loadtest` |
| 规格 | 新建 `agent-super-agent`、`agent-user-memory`；delta `agent-runtime-paths`、`platform-chat`、`agent-sandbox` |
| 测试 | `test_deep_research_prompt.py` → `test_super_agent_prompt.py`；filesystem / memory 集成测 |
