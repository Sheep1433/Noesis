# 决策：Skills 市场详情：仅 skills.sh，不展示包内目录

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **约定**：浏览/榜单/详情走 skills.sh；安装仍走 GitHub zip；完整目录结构仅在「已安装」Tab 查看。
- **实现**：`fetch_skill_preview` 只 GET `skills.sh/{source}/{skill_id}`，解析 `prose` 正文 + schema.org `display_name`；响应去掉 `skill_tree`；前端详情区移除目录树，提示安装后去已安装查看。
- **清理**：删除 GitHub trees/Contents 预览与 `_build_market_tree` 等死代码。
