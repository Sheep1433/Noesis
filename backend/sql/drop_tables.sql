-- 清空 Noesis 业务表（重建前执行，使用前请备份）
-- 表结构以 Alembic 为准：uv run alembic upgrade head

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS `t_chat_attachment`;
DROP TABLE IF EXISTS `t_chat_message`;
DROP TABLE IF EXISTS `t_chat_session`;
DROP TABLE IF EXISTS `t_user`;
DROP TABLE IF EXISTS `alembic_version`;

SET FOREIGN_KEY_CHECKS = 1;
