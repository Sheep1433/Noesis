# 决策：流式落库原则：durable vs ephemeral（streaming-persistence-principle）

状态：implemented
日期：2026-08-06
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **问题/症状**：原 spec 只约束"assistant 正文按骨架—检查点—终态单次落库"，未定义 tool output chunk、reasoning raw delta、progress 等流式内容的入库边界；各写入点自行判断粒度 → 行膨胀与恢复困难。
- **根因**：流式系统把"传输的细粒度事件"和"应持久化的语义单元"混为一谈；过程增量不具备恢复价值，却比终态更快积累。
- **解法/原则**：明确二分——**durable**（终态 content snapshot + 生命周期事件）入库持久化；**ephemeral**（流式 chunk / raw delta / progress / typing / heartbeat）不持久化。`PersistSink` 已按语义边界节流、写完整 content 快照而非增量，行为符合，故本 spec 为约束性文档化（不改表结构/代码）。
- **边界**：不区分"谁产生"（LLM/tool/progress），只看"是否终态语义"。
- **可迁移**：设计流式持久化先问"这一片断下来还能不能恢复上下文？"，能→持久化，不能→丢弃。粒度由语义单元（整条 content / 生命周期状态）决定，不由传输频率决定。
- **验证与遗留**：待任务闭环（/goal）。参考：`openspec/changes/streaming-persistence-principle/`。
