# 决策：AutoDream + 消息水位衔接

状态：implemented
日期：2026-08-26
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **机制**：每 Run 结束后触发记忆抽取（对齐 extractMemories），抽取时取该 Run 的前 2 条消息做「水位」——给后续注入提供会话开头的上下文锚点，避免抽取只看尾部信息失去全景。
- **约束**：subagent 不抽（内容太碎、无长期价值）；记忆抽取/注入不得影响会话列表排序（不改 `updated_at`）；旧做梦流程（memory/YYYY-MM-DD.md）已删除。
- **scope**：SuperAgent 沙箱恒 global（scope 修正已在 8/25 完成）；抽取任务同用户串行（防并发写覆盖）。
