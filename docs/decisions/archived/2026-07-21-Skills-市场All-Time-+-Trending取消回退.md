# 决策：Skills 市场：All Time + Trending，取消回退

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **排序**：`GET /market/browse?sort=trending|all_time` → 抓 `skills.sh/trending` 或 `skills.sh/`。
- **无回退**：榜单失败直接报错，不再用 featured 种子顶上。
- **UI**：市场 Tab 提供 Trending / All Time 切换。
