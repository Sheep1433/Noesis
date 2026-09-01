# 决策：Skills 市场对接 skills.sh

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **选型**：接 skills.sh（跨 Agent 公共目录），不接 ClawHub（偏 OpenClaw）。
- **发现**：`GET /api/skills/market/search` → `skills.sh/api/search`；`/market/browse` 用配置 `skills_market.featured_skills` 推荐种子。
- **安装**：`POST /api/skills/market/install` 从 GitHub zipball 抽出含 `SKILL.md` 的目录 → 写入 `.noesis/users/{uid}/skills/`，写 `.skills-sh/origin.json`，bump revision。
- **UI**：扩展页 Skills 增加「已安装 / 市场」Tab；装完回到已安装树。
- **配置**：`backend/config.yaml` → `skills_market.*`（base_url / timeout / featured_skills）。
