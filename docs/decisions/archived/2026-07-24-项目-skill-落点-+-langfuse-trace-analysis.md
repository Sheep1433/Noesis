# 决策：项目 skill 落点 + langfuse-trace-analysis

状态：implemented
日期：2026-07-24
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 轨迹排障流程被写成 skill 时误放进 `knowledge-base/skills/`；该流程绑 Noesis DB/Langfuse/部署，离开仓库不可用。

**How to apply：**
- 跨工具个人 skill → `knowledge-base/skills/` → `~/.agents` 软链
- Noesis 专用 → `Noesis/.agents/skills/<name>/`；Cursor 用 `.cursor/skills/<name>` **软链**
- `langfuse-trace-analysis`：对照 Langfuse UI + 业务 DB，扫 execute 失败文本（timeout/cd、权限、缺 node 等）
- **禁止**把仓库专用流程写进 knowledge-base；误推 `.agents` 到公开 GitHub 须立刻回退
