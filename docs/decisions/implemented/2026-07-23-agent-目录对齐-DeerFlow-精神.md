# 决策：agent/ 目录对齐 DeerFlow 精神

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** `hitl/` 把策略工具与平台 pending 混在一起；`base/` 空壳；入口散在根上。

**How to apply：**
- `profiles/`：Super / QA / 故障 / MCP 入口
- `guardrails/`：policy + session_grants
- `tools/ask_user.py`：ask_user + interrupt_on
- `domain/chat/hitl/`：pending / timeout（平台态）
- `domain/chat/streaming/hitl.py`：SSE 载荷组装
- `base_agent.py` → `profiles/base_agent.py`；`skills_filter.py` → `backends/skills_filter.py`（不放 `agent/` 根）
- **无**旧路径 re-export
