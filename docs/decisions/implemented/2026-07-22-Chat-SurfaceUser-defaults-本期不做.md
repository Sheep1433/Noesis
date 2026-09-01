# 决策：Chat Surface：User defaults 本期不做

状态：implemented
日期：2026-07-22
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- 跨会话默认模型/MCP/Skills/KB 仍只靠平台缺省 + COMPOSING 内存 overlay + 发送后 `session.extra`。
- 个人默认若以后要做，挂 `add-agent-user-settings`；TestAssistant 不在本 lifecycle 收编范围。
