-- 清空 Noesis 业务表（重建前执行，使用前请备份）
-- 表结构以 Alembic 为准：uv run alembic upgrade head

DROP TABLE IF EXISTS t_chat_attachment CASCADE;
DROP TABLE IF EXISTS t_chat_message CASCADE;
DROP TABLE IF EXISTS t_chat_session CASCADE;
DROP TABLE IF EXISTS t_user_session CASCADE;
DROP TABLE IF EXISTS t_user CASCADE;
DROP TABLE IF EXISTS kb_collection_config CASCADE;
DROP TABLE IF EXISTS alembic_version CASCADE;
