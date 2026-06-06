-- =============================================
-- Noesis 数据库清表脚本
-- 用于删除所有表（重建前执行）
-- 使用前请确保已备份数据！
-- =============================================

-- 禁用外键检查，避免删除顺序问题
SET FOREIGN_KEY_CHECKS = 0;

-- 按依赖关系顺序删除表（子表先删）
DROP TABLE IF EXISTS `t_chat_message`;
DROP TABLE IF EXISTS `t_chat_session`;
DROP TABLE IF EXISTS `t_test_case`;
DROP TABLE IF EXISTS `t_demand`;
DROP TABLE IF EXISTS `t_user_skill`;
DROP TABLE IF EXISTS `t_user`;

-- 恢复外键检查
SET FOREIGN_KEY_CHECKS = 1;
