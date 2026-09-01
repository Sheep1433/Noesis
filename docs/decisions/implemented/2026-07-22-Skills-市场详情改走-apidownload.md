# 决策：Skills 市场详情：改走 /api/download

状态：implemented
日期：2026-07-22
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **动机**：HTML/RSC 解析脆弱且曾截断；`/api/download` 直接返回 `SKILL.md` 原文，更稳更快。
- **实现**：`fetch_skill_preview` → `GET skills.sh/api/download/{source}/{skill_id}`，只取 `files[]` 中 `SKILL.md`；`name` 从 frontmatter 读；仍不写本地、不展示包内目录。
- **清理**：移除详情页 HTML / RSC 解析辅助函数。
