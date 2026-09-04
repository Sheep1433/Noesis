# 决策：SuperAgent 用户记忆（add-super-agent-user-memory 归档）

状态：implemented
日期：2026-07-10
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **BREAKING**：`DEEP_RESEARCH_QA` / `DeepResearchAgent` 移除；统一为 `SUPER_AGENT_QA` + `SuperAgent` + `task-worker`。
- **磁盘**：`.noesis/users/{uid}/AGENTS.md`（Agent 可写）、`USER.md`（Agent 只读）；删 session **不**删记忆文件。
- **虚拟路径**：`/memory/AGENTS.md`、`/memory/USER.md` → `UserMemoryBackend`；`MemoryMiddleware` + `MemorySyncMiddleware` 仅主 Agent 挂载。
- **面板**：右侧上下文树展示两文件；`PUT workspace/file` 允许用户直接编辑 `AGENTS.md` 与 `USER.md`。
- **Agent 写边界（2026-07-10 更新）**：`USER.md` 与 `AGENTS.md` 均可由 Agent `edit_file` 更新（对齐 OpenClaw「USER.md Update as you go」）。
- **规格**：主 spec 新增 `agent-super-agent`、`agent-user-memory`；删除 `agent-deep-research`。
- **测试**：`test_super_agent_memory.py`、`test_user_memory_*`、面板写 `USER.md` 用例。
