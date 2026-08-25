-- 清空 Noesis 业务库（重建前执行，使用前请备份）。
-- 该脚本会删除 public schema 下的全部对象，表结构以 Alembic 为准。

DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
