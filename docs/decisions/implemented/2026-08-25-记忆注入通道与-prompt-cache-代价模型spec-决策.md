# 决策：记忆注入通道与 prompt cache 代价模型（spec 决策）

状态：implemented
日期：2026-08-25
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **问题**：每 Run 注入新记忆是否会显著降低 prompt cache 命中。
- **机制**：cache 是前缀匹配，判定只看「注入后是否再变」与「位置」。三种放置方式：追加持久化（零损失）、临时末尾插入（损失上一轮 token，有界不随历史增长）、system 前缀区每轮变化（全历史重算，唯一红线）。
- **Noesis 现状**：`late_context.insert_late_context` 是临时末尾插入，槽位里 `current_time` 本就每 Run 变——记忆放同一槽位零边际成本；Run 内部首调用付一次代价、后续全命中。
- **落进 spec**：「注入通道与 prompt cache」小节；硬约束一条——**每 Run 变化内容 SHALL NOT 进稳定前缀区**；「持久 reminder 消息」降级为后续优化项；注入 = 每 Run 选条（稳定前缀 USER.md+索引 + 廉价模型选条 + alreadySurfaced 去重 + stale 警告）。
- **同轮校正**：「蒸馏」→「记忆抽取」（对齐 extractMemories）；撤回索引分层 → 单层索引（一行一条）+ 行数/字节双保险预算，超预算走整理压缩——对齐 Claude Code MEMORY.md 设计（它用压缩而非分层解决膨胀）。
- **可迁移**：分析 cache 影响区分三个变量——注入通道、变化频率、位置；方案引用业界设计前先读原始实现。
