# 决策：Composer `/` `@` mentions（slash / at）

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **动机**：对齐 Cursor 输入内快速引用；Web 无本机索引，用预取 catalog + 本地 fuzzy + 结构化 `mentions`。
- **协议**：请求 `extra.mentions[]`（`skill|file|folder|subagent`）→ `MentionResolveService` 校验归属/穿越 → prompt `<user_mentions>` 注入；user 消息 `extra.mentions` 落库。
- **范围**：SUPER 全开；FAULT 仅 file/folder/subagent（skill → 4xx）；COMMON/TEST 拒 mentions。
- **性能**：复用 `GET /api/skills/fs/tree` + session context；TTL 缓存；按键不打全量树。
- **UI**：`MentionPicker`；选中/Tab 将 `/skill`、`@path` **直接写入输入框**（无上方 chip）；`/` 仅**行首**，`@` 为**空白边界**（可一句内挂多文件）。历史气泡仍可只读展示 `extra.mentions`。OpenSpec：`add-composer-slash-mentions`。
