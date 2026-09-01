# 决策：add-agent-user-settings：设置壳 + 记忆/cron/通道配置

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 侧栏只有退出；记忆编辑散在会话面板；需要用户级 cron 与通道凭据入口，且与 Delivery 运行时拆清。

**How to apply：**
- 路由 `/settings?s=`；侧栏头像 → 设置；section 无 slash。
- API：`/api/user/memory/{USER.md|AGENTS.md}`、`/scheduled-tasks`、`/channels`（Cookie Session + CSRF）。
- L2：`users/{uid}/memory/YYYY-MM-DD.md` 目录 ensure；不默认注入。
- Cron：表 `user_scheduled_tasks` + 进程内 30s 轮询 + SKIP LOCKED；会话删除停用 `session:{id}` 绑定。
- Channels：`channels.json` 脱敏；同步 `ChannelBindingStore`；Agent `/memory/` 白名单无 channels。
- 部署后：`uv run alembic upgrade head`。
