## 1. 后端：用户级 Memory API

- [ ] 1.1 新增 `GET/PUT /api/user/memory/{file}`（`USER.md` | `AGENTS.md`），Service 读写 `user_data_paths`，ResponseUtil 封装
- [ ] 1.2 补充路径辅助：`get_user_daily_memory_path`、确保 `memory/` 目录（L2 可先只建路径不接 UI）
- [ ] 1.3 回归测试：设置 API 与面板 PUT、Agent `/memory/` 同盘；401/非法 file 名

## 2. 后端：定时任务

- [ ] 2.1 Alembic 迁移 `user_scheduled_tasks`（或等价表）与 ORM/schema
- [ ] 2.2 实现 `/api/user/scheduled-tasks` CRUD、启停、列表；校验 cron 表达式与 `qa_type`
- [ ] 2.3 实现调度器（startup 注册）+ 多实例防重入；到期触发 isolated Agent 跑次并更新 `last_*`
- [ ] 2.4 会话删除/归档时停用 `session:{id}` 绑定任务；用户删除级联清理
- [ ] 2.5 测试：非法 cron 400、越权 404、停用后不触发、绑定会话删除后 disabled

## 3. 后端：通讯通道（配置契约优先）

- [ ] 3.1 持久化模型（DB 表或加密 `channels` 存储）+ `/api/user/channels` CRUD；token 脱敏回显
- [ ] 3.2 强制：通道密钥不可经 Agent 文件工具写入；单测覆盖拒绝路径
- [ ] 3.3 Telegram：保存/启停配置；入站配对与 webhook **可标 follow-up**（本 change 至少配置面可用）

## 4. 前端：设置壳

- [ ] 4.1 路由 `/settings`、`SettingsShell` + `SettingsNav`（`s=` section）；侧栏头像入口（保留退出）
- [ ] 4.2 `overview` / `account` / `capabilities`（深链 Skills、MCP、知识库）
- [ ] 4.3 `profile` / `memory`：对接用户级 memory API；展示修改时间；**无 slash UI**
- [ ] 4.4 `automation`：任务列表与创建/编辑/启停表单
- [ ] 4.5 `channels`：Telegram 通道表单（脱敏展示）；runtime 未通时状态文案
- [ ] 4.6 会话上下文面板：记忆文件「在设置中打开」可选入口；`pnpm lint` 影响范围

## 5. 文档与验收

- [ ] 5.1 更新 `frontend/AGENTS.md` 页面表；必要时 `docs/NOTES.md` 追加知识卡片
- [ ] 5.2 手动验收：设置改 USER/AGENTS → Agent 新会话可见；创建 cron → 手动 run/到期状态；通道保存脱敏
- [ ] 5.3 确认设置导航无 slash；`uv run app.py` 可启动
