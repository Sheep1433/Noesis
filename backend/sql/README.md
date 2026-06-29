# 数据库（Alembic）

表结构与初始数据统一由 **Alembic** 管理。

## 全新库

```bash
cd backend
uv run python sql/initialize_mysql.py   # 建库 + migrate + 演示账号
```

或库已存在时：

```bash
cd backend
uv run alembic upgrade head
```

演示账号：`admin` / `123456`（由 `202606290001_initial_schema` 写入，部署后请改密）。

## 日常改表

```bash
cd backend
# 1. 改 models/
uv run alembic revision --autogenerate -m "describe_change"
# 2. 人工审阅 versions/*.py
uv run alembic upgrade head
```

`uv run app.py` / `./scripts/run.sh dev` / Docker 后端启动时会在 `init_database()` 中执行 `upgrade head`。

## 清空重建

```bash
mysql -u root -p noesis < sql/drop_tables.sql
uv run alembic upgrade head
```

## 回滚

```bash
uv run alembic downgrade -1
uv run alembic downgrade base
```
