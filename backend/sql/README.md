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

## 经验记忆迁移

经验记忆未上线的旧表与按日文件不迁移。`202608240001_reset_unreleased_memory` 只识别并删除旧原型表，再从空模型创建当前 snapshot/item/evidence/relation/job/outbox；不会读取或转换旧数据。用户 preference 独立保留，默认 `enabled=false`。

部署顺序：

```bash
cd backend
uv run alembic upgrade head
# 后端 lifespan 启动唯一的 machine-memory worker
```

迁移后先保持用户开关关闭。需要恢复派生数据时，从 PostgreSQL desired state 重建服务端 memory workspace 和 `noesis_memory` collection；两者都不是事实源。关闭经验记忆不需要回滚 schema，也不会影响 `USER.md`、`AGENTS.md` 或聊天数据。
