# 决策：Skills 市场推荐改为 skills.sh 趋势榜

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **原因**：网页 Leaderboard 即趋势；原先对 featured 搜 8 次是多余。
- **实现**：`fetch_trending` 一次 GET 首页 HTML，解析 SSR 榜单（rank + installs）；缓存 `cache_ttl`；`site/*` 非 GitHub 源跳过。
- **回退**：解析失败仍用配置 `featured_skills`。
