# 决策：Chat Surface P1：/chat/:sessionId 续聊

状态：implemented
日期：2026-07-22
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **路由**：`ChatIndex`（`/chat`）、`ChatNew`（`/chat/new`）、`ChatSession`（`/chat/:sessionId`）。
- **行为**：发送 ensure 成功后 `replace` 到 ChatSession；历史点选同步 URL；刷新 ACTIVE 经 `getSession` + `loadSessionMessages` 恢复；无 user 消息的深链回新对话；`newChat` → ChatNew。
- **导出**：`loadSessionMessages` 供路由恢复复用。
