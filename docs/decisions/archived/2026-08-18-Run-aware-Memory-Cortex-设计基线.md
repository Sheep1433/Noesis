# 决策：Run-aware Memory Cortex 设计基线

状态：implemented
日期：2026-08-18
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**问题/症状：** Noesis 原有记忆更接近文件记忆或 RAG 包装，难以沉淀工具失败、技术决策、验证结果和跨 Run 的经验；如果每轮同步调用 LLM 提取，成本和上下文扰动都不可控。

**设计结论：** 记忆主对象应是 `Run experience`，而不是原始聊天文本。PostgreSQL 保存 typed、temporal、evidence-backed 状态和关系，Qdrant 只做派生语义索引；文件保留人工 Identity/Policy。Run 结束后异步 Reflect，Compaction 前提取 decision/experience，session-start 生成受 token budget 控制的 Memory Bulletin。

**可迁移原则：** recall 负责找证据，reflect 负责综合，get_source 负责回溯原始 Run；不要把 top-k 原始片段直接塞进 system prompt。记忆写入要有 provenance、版本和冲突修订，只有重复成功且经过验证的经验才进入 Skill candidate。

**验证与遗留：** 研究报告 `docs/research/agent-memory-2026/reports/final-report.md` 已归档；建议用 LongMemEval、MemoryAgentBench、PrecisionMemBench 和 Noesis RunMemory 场景验证跨会话召回、过期事实抑制、证据覆盖率和 token 成本，当前尚未实现 Cortex。
