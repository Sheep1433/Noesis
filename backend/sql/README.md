# 数据库（Alembic）

表结构与初始数据统一由 **Alembic** 管理。

## 全新库

```bash
cd backend
uv run python sql/initialize_postgresql.py   # 建库 + migrate + 演示账号
```

或库已存在时：

```bash
cd backend
uv run alembic upgrade head
```

演示账号：`admin` / `123456`（由 `202606290001_initial_schema` 写入，部署后请改密）。

## 轮换注册邀请码

注册使用管理员持有的一个全局 6 位数字邀请码；邀请码可重复使用，轮换后旧码立即失效：

```bash
cd backend
uv run python sql/rotate_registration_invite.py
```

默认更新 `admin` 用户的邀请码；如管理员用户名不同，可传 `--admin-username <用户名>`。邀请码明文只会在命令输出中显示一次。

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
psql "$POSTGRES_URL" -f sql/drop_tables.sql
uv run alembic upgrade head
```

## 回滚

```bash
uv run alembic downgrade -1
uv run alembic downgrade base
```
