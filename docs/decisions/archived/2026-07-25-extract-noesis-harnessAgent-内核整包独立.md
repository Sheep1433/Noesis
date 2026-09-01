# 决策：extract-noesis-harness：Agent 内核整包独立

状态：implemented
日期：2026-07-25
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 评测/通道要调 Agent 内核，但不能反向依赖平台 `services`/Delivery；旧「整包搬家 + AgentRunService」与 Delivery 重叠故搁置。对齐 DeerFlow：`packages/harness` = 内核，Gateway/QaService = 壳，同 factory、异投递。

**How to apply：**
- 代码：`backend/packages/harness/noesis/`（import `noesis`）；不保留旧命名空间 shim
- 依赖：`noesis -X→ services`、`-X→ domain.chat.delivery`；附件/KB 经 `services/harness_wiring.wire_harness_platform_deps` → `noesis.runtime.deps`
- 流式核：`noesis.runtime.stream.stream_agent_events`；Harbor/BrowseComp 同核
- Delivery 仍在 `domain/chat/delivery`；**不要**再造 RunManager
- OpenSpec：`openspec/changes/extract-noesis-harness/`
- 回归：`cd backend && uv run pytest tests/ -q`
