# 决策：Skills 市场详情：解析 previewHtml + restHtml

状态：implemented
日期：2026-07-22
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **现象**：详情只到「2. Tech Stack」并混入 `Show more` / Installs 等侧栏文案。
- **根因**：skills.sh SSR 的 `prose` 区只渲染折叠前预览；完整正文在 RSC flight 的 `previewHtml` + `restHtml`（`$34`/`$35` 槽位）。
- **修复**：`_extract_preview_rest_html` 拼接两段 HTML 再转 Markdown；SSR `prose` 仅作回退。
