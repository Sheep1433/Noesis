# 决策：Agent 路径唯一规则（防 workspace/workspace）

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** Agent 混用虚拟根 `/research/...`、容器 `/workspace/...`、UI `sessions/{sid}/workspace/...`，`PrefixBackend` 再叠 `/workspace` → 出现双目录；`write_file` 失败用户侧只见「执行失败」。不应靠改 Skill 文案或再叠 middleware 补丁。

**How to apply：**
- **唯一入口**：`sandbox_mount_policy.canonicalize_agent_path`；`PrefixBackend._map_in`、mention→虚拟路径、HITL memory 判定共用。
- **坐标系**：filesystem 虚拟根=`/`；容器物理=`/workspace`（execute cwd，**不** rewrite shell）；UI=`sessions/{sid}/workspace/`。
- **别名剥除**：`/workspace/...`、`sessions/*/workspace/...`（可重复剥）；`/skills|/memory|/uploads|/attachments` 不剥。
- **禁止**：改 `extensions/skills/**`；新增路径 middleware；对 execute 做 shlex 路径改写。
- 平台 prompt 一句纪律见 `agent/prompts/execution.py`（非 Skill）。
