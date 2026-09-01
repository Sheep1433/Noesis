# 决策：MySQL → PostgreSQL 生产迁移

状态：implemented
日期：2026-07-13
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **动机**：统一业务库与 LangGraph checkpoint 到 PostgreSQL；Compose 内建 `postgres:17`，去掉宿主机 `noesis-mysql` 容器。
- **代码**：`database.py` / `env.py` 改 `asyncpg`+`psycopg`；`checkpointer` 用 `noesis_langgraph` 库；Alembic 迁移与 `initialize_postgresql.py`；JWT 改为服务端 Session（`auth_api` + `user_sessions` 表）。
- **部署**：`deploy/docker-compose.yml` 增 `postgres` 服务；`config.docker.yaml` 中 `database.host: postgres`；`deploy/postgres/init.sql` 建 checkpoint 库；`.env.docker` 用 `POSTGRES_PASSWORD`（宿主机映射端口与 Langfuse 冲突时用 `POSTGRES_PORT=5433`）。
- **注意**：线上 MySQL 卷已删则无法自动迁数据，需空库 Alembic 初始化；CI `main` 通过后 `deploy-remote.sh` 自动重建栈。
