# 决策：Deep Research：HITL 占用 Run 预算 + 上下文膨胀

状态：implemented
日期：2026-08-04
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** `/deep-research-v2` 跑到 Phase 5 正准备生成最终报告时被 900s watchdog 切断，用户只拿到 `partial` 无交付物。

**根因（详见 `docs/bug/deep-research-hitl-timeout-and-context-bloat.md`）：**
1. Run 从启动即计时，HITL 等待约 237s 也占用 900s 预算 → `RUN_TIMEOUT / limit_exceeded`。
2. HITL 恢复使首批 3 个并行 task 以新 `tool_call_id` 重建；子 Agent 全量事件回灌父消息 → assistant 单条 2.59MB / 89,815 tokens。
3. Skill 是领域无关刚性清单（默认 deep、≥20 来源、强制论文/竞品/政策），LLM Wiki 主题不适配仍全量跑。
4. 搜索不稳定放大重试：Tavily SSL 失败落 DDG、知乎 403、GitHub rate limit、44 次 fetch 4 次 error。

**可迁移原则：**
- HITL 等待不应计入执行预算（或预算只计模型/工具活跃时间）；watchdog 要区分「等待用户」和「执行中」。
- 子 Agent 轨迹按需回灌（只留决策摘要/关键证据），不要全量进父消息；`status=success` 不可信，要强扫工具输出文本。
- 可观测系统会自我膨胀：远程服务器磁盘 100% 的长期根因是 ClickHouse `system.trace_log`(4.73GB)+`system.text_log`(2.89GB) 等诊断表（约 9.4GB），业务 `observations` 仅 177MB；Docker build cache 只是触发点。清磁盘先清可重建资源，不碰业务 volume。
- Skill 设计：研究范围/深度应由主题适配，不能用固定刚性清单。
