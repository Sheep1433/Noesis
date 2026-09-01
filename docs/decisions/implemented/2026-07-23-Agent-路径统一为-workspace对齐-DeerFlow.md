# 决策：Agent 路径统一为 /workspace（对齐 DeerFlow）

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 旧「虚拟根 `/notes.md`」+ 容器 `/workspace` + UI `sessions/...` 三套坐标迫使 PrefixBackend/canonicalize 互相剥前缀。

**How to apply：**
- Agent 与 Shell 共用 ``/workspace/...``、``/skills/...``、``/memory/...``
- `canonicalize`：裸路径/UI → `/workspace/...`；折叠双 workspace
- docker：default=沙箱（skills 挂载在容器内，不再单独 route）；local：strip `/workspace` + skills routes
- Prompt / mention 注入已切新路径
