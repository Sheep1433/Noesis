# 决策：SuperAgent prompt：复杂任务默认委派 task-worker

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **动机**：对话六七轮 + 主线程连环 `web_search`/`web_fetch` 易触上下文上限；暂不改 middleware/配置，先用 prompt 约束行为。
- **改动**：`prompts/super_agent.py` 翻转策略——轻量（≤2 次工具）主 Agent 自做；多源检索/调研/多步实现优先 `task-worker`；委派须自包含、禁懒委派；子 Agent 小结默认 ≤400 字、长文落盘。`task-worker` tool description 同步强调优先委派。
- **局限**：仅靠 prompt，主 Agent 仍持有 web/fs 工具，模型可能继续自己搜；若仍爆窗，再做 eager offload / 工具白名单。
