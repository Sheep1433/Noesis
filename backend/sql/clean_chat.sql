-- =============================================
-- Noesis 清理聊天记录脚本
-- 用于清空调试/测试数据，保留表结构
-- 使用前请确保已备份数据！
-- =============================================

USE `noesis`;

-- 禁用外键检查，避免删除顺序问题
SET FOREIGN_KEY_CHECKS = 0;

-- 按依赖关系顺序清空表（子表先清）
TRUNCATE TABLE `t_chat_attachment`;
TRUNCATE TABLE `t_chat_message`;
TRUNCATE TABLE `t_chat_session`;

-- 恢复外键检查
SET FOREIGN_KEY_CHECKS = 1;

-- 可选：查看剩余数据量
-- SELECT COUNT(*) AS message_count FROM t_chat_message;
-- SELECT COUNT(*) AS session_count FROM t_chat_session;
