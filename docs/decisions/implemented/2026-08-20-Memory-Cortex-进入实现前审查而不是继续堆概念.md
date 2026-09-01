# 决策：Memory Cortex 进入实现前审查，而不是继续堆概念

状态：implemented
日期：2026-08-20
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**问题/症状：** Memory Cortex 设计方向正确，但多轮审查发现“确定性修订”仍依赖 LLM 生成 topic/content，scope 没进入记忆身份，outbox 唯一键会吞掉 disable/delete 状态变化，多实例 job/lease fencing 也未闭合。

**解法/取舍：** 暂停直接开发，先把设计修到可实施：确定性 `subject_key`/`resolution_key` 与 scope 进入唯一身份；重复内容要有 NOOP；状态变化使用独立 outbox event/version，不复用 embedding index version；job claim 需要 lease/fencing 和幂等状态迁移；失败经验、修复方式、provider/tool/environment 需保留 evidence。

**可迁移原则：** 记忆系统的难点不是“如何召回”，而是身份稳定、修订安全、异步索引不丢状态和多实例并发正确。设计文档宣称“确定”前，必须逐项证明 key、锁、outbox、job retry 和 scope 都是确定的。

**验证与遗留：** 当前仍处于设计审查阶段；PostgreSQL 事实源 + Qdrant 派生索引的边界保留，但必须先补齐上述 P0，再进入 OpenSpec 和实现。
