# 决策：backends 职责收拢（保留名 backends）

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** Skills 过滤 / 死代码 StaticListing / 薄 sandbox_common / 错误 re-export 把「执行后端」目录弄脏。

**How to apply：**
- `agent/skills/`：`SKILL_SOURCES` + 会话过滤（离开 backends）
- `path_policy`（原 sandbox_mount_policy）；`memory_backend`（原 backend_guards，删 StaticListing）
- `sandbox_common` 并入 `docker_exec_sandbox`；去掉兼容 shim
- `agent_filesystem` 只导出 `build_agent_filesystem_backend`
