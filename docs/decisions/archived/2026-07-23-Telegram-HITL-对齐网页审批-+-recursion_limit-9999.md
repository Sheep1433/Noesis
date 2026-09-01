# 决策：Telegram HITL 对齐网页审批 + recursion_limit 9999

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** TG 撞 HITL 只能提示去网页；且「跑几轮」就到 200——LangGraph `recursion_limit` 计的是图节点步（模型轮+工具节点+中间节点），不是用户对话轮。

**How to apply：**
- HITL：`hitl_prompt.py` 发卡 + Inline Keyboard（批准/拒绝/本会话放行）；`callback_query` → `resume_channel_hitl`（与网页 decisions/grant_scope 同语义）；clarification 用下一条文字 respond。
- `DEFAULT_RECURSION_LIMIT = 9999`（`agent/base/base_agent.py`）。
- Poll `allowed_updates` 含 `callback_query`。
