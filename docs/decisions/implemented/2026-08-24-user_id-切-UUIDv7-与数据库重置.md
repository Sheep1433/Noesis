# 决策：user_id 切 UUIDv7 与数据库重置

状态：implemented
日期：2026-08-24
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **动机**：自增 id 在侧边栏暴露用户序号；AI Agent 产品数据可整体清空重建，切换成本低。
- **变更**：user_id 改 UUIDv7；清空 `noesis` 业务库与 `noesis_langgraph` checkpoint 库并重新 Alembic 初始化；初始化 admin 用户（UUIDv7 已验证）。备份 dump 留 /tmp。
- **脚本**：`initialize_postgresql.py` 曾加 `--reset` 后删除（避免误触发全量重置），保留正常迁移功能；`drop_tables.sql` 改为完整重置 public schema。
- **边界**：Qdrant、`.noesis` 目录、Docker volume 未清理。
