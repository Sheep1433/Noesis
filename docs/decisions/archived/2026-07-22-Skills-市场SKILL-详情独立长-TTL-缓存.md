# 决策：Skills 市场：SKILL 详情独立长 TTL 缓存

状态：implemented
日期：2026-07-22
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **机制**：进程内 dict + `time.monotonic()`；key=`{source}/{skill_id}`；仅 `fetch_skill_preview`（/api/download）使用 `preview_cache_ttl_seconds`。
- **默认**：搜索/榜单 `cache_ttl_seconds=300`；SKILL 详情 `preview_cache_ttl_seconds=86400`（24h）。
- **配置**：`skills_market.preview_cache_ttl_seconds` 或环境变量 `SKILLS_MARKET_PREVIEW_CACHE_TTL_SECONDS`；`0` 关闭缓存。
