# 决策：记忆 scope 修正：沙箱工作区下 project_key 恒为 global

状态：implemented
日期：2026-08-25
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **问题/症状**：验证 SuperAgent 记忆功能时发现「项目级记忆」不存在——`resolve_project_key` 恒返回 `global`，scope 恒为 `profile:SUPER_AGENT:global`。
- **根因**：design.md Decision 13 按「Agent 跑在 Git 仓库」假设写了三条 Git-based project key 规则，但 SuperAgent 工作区是 `.noesis/users/{uid}/sessions/{sid}/workspace` **每会话一次性沙箱**（`agents/backends/factory.py:109`），里面永远没有 .git/origin → 规则与产品现实脱节，无 origin 分支还会生成指向临时目录 digest 的死胡同 scope。
- **解法**（实现 B + A）：
  - `services/memory/scope.py`：带 origin 的 Git 仓库 → origin digest（跨会话共享）；其余（含无 origin 沙箱仓库）→ `global`。
  - 三处文档 + tasks 对齐新规则；补「无 origin 沙箱归 global」及跨会话死胡同回归用例。
- **可迁移**：spec 的 scope/身份规则必须基于运行时**真实输入**推演（这里是每会话沙箱路径），不能只按理想部署形态写规则；给用户的操作剧本先对照代码核实关键假设再交付。
- **验证**：`uv run pytest tests/ -q` 1194 passed；`openspec validate --strict` 通过。
