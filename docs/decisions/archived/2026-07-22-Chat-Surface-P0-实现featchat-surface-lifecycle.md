# 决策：Chat Surface P0 实现（feat/chat-surface-lifecycle）

状态：implemented
日期：2026-07-22
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **前端**：ModelSelector / KbScopeSelector / Toolbar 增加 `persistSessionExtra`；COMPOSING 不 ensure；发送前 `ensureSession(buildComposingSessionExtra)`；FAULT 隐藏上传入口且不用 kb 即时上传。
- **后端**：`get_user_sessions` / `query_user_sessions_for_record` 增加「至少一条未删 user 消息」exists 过滤。
- **测**：`tests/test_session_list_hides_empty.py`。
