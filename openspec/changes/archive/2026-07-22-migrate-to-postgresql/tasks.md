## 1. PostgreSQL 基础设施与依赖

- [x] 1.1 在 `backend/pyproject.toml` 与锁文件中配置 PostgreSQL async/sync 驱动及 LangGraph PostgreSQL checkpointer 依赖。
- [x] 1.2 更新 `deploy/docker-compose.yml`，添加带持久化卷与健康检查的 PostgreSQL 服务，并让 backend 在数据库就绪后启动。
- [x] 1.3 更新 `deploy/backend/Dockerfile` 的构建和运行时系统依赖，满足 PostgreSQL 驱动运行需求。
- [x] 1.4 更新 Docker、本地和生产示例 env/YAML，定义唯一权威的 `POSTGRES_*` 与 LangGraph PostgreSQL 连接配置。

## 2. 业务 PostgreSQL 持久化

- [x] 2.1 改造 `backend/config/env.py`、`backend/config/yaml_config.py` 和 `backend/config/database.py`，构造 SQLAlchemy `asyncpg` 业务 URL 与 `psycopg` 同步/Alembic URL，并保留连接池和启动校验语义。
- [x] 2.2 提供 PostgreSQL 初始化入口：创建已配置业务数据库、执行 Alembic upgrade，并保持演示账号初始化可用。
- [x] 2.3 审查并改造全部 Alembic 版本迁移及 `alembic/env.py`，确保 PostgreSQL 兼容，并在空 PostgreSQL 数据库执行 `upgrade head` 验证。
- [x] 2.4 审查同步数据库访问（包括 `kb_collection_config_service`）与模型类型/默认值，确保 CRUD、JSON、索引、外键和时序主键在 PostgreSQL 下等价工作。

## 3. LangGraph PostgreSQL checkpoint

- [x] 3.1 在 `backend/config/checkpointer.py` 使用 PostgreSQL async saver 与连接池，实现 lifespan 初始化 `setup()` 和安全关闭。
- [x] 3.2 使用独立 PostgreSQL checkpoint 数据库，并确保其不与业务库共享表或权限边界。
- [x] 3.3 更新 checkpointer 生命周期、恢复执行和 context metrics 集成测试，验证多个后端实例可读取同一 thread 的已提交 checkpoint。

## 4. 回归验证与文档

- [x] 4.1 为 PostgreSQL 配置、Alembic 初始化和业务数据库不可用添加自动化测试。
- [x] 4.2 回归验证用户认证、会话/消息 CRUD、知识库集合配置，以及 `/api/chat/sessions/stream` 的 SSE 事件兼容和 assistant 单行终态落库。
- [x] 4.3 更新 `README.md`、`deploy/README.md`、`backend/sql/README.md`、后端开发说明和配置注释，使 PostgreSQL 成为唯一关系型数据库说明，同时保留 Qdrant 与附件文件存储职责说明。
- [x] 4.4 执行 `cd backend && uv run pytest tests/ -q`、`cd backend && uv run app.py` 的 PostgreSQL 冒烟验证；若前端未改动，记录无需前端构建的依据。
