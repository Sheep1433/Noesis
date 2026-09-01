# 决策：Telegram 终态 MarkdownV2（最小可用）

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** plain 看得到流式，但 `**粗体**` / 代码块不排版。

**How to apply：** 流式仍 plain；`force_close` / 兜底发送走 `to_telegram_markdown_v2` + `parse_mode=MarkdownV2`，失败回落 plain。见 `telegram/markdown_v2.py`。
