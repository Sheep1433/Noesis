CREATE DATABASE IF NOT EXISTS `noesis` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `noesis`;

-- ============================================================================
-- 用户表
-- ============================================================================
DROP TABLE IF EXISTS t_user;
CREATE TABLE t_user (
    id INT NOT NULL AUTO_INCREMENT COMMENT '用户ID',
    username VARCHAR(200) COMMENT '用户名称',
    password VARCHAR(300) COMMENT '密码(bcrypt)',
    mobile VARCHAR(100) COMMENT '手机号',
    create_time DATETIME COMMENT '创建时间',
    update_time DATETIME COMMENT '修改时间',
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户表';

-- 演示账号（开源/本地初始化）：用户名 admin，密码 ChangeMe123! — 部署后请立即修改
INSERT INTO t_user (id, username, password, mobile, create_time, update_time)
VALUES (1, 'admin', '$2b$12$o03ZpU4PRCGU8h4Ra1JrQOZn/npzuXpBXnakhCgKWaxuP.BEufgkK', NULL, NOW(), NOW());

-- ============================================================================
-- 会话表 v2.1
-- ============================================================================
DROP TABLE IF EXISTS t_chat_session;
CREATE TABLE t_chat_session (
    id VARCHAR(36) NOT NULL COMMENT 'UUID 主键',
    parent_id VARCHAR(36) COMMENT '父会话 ID（subagent 场景）',
    user_id VARCHAR(36) NOT NULL COMMENT '用户 ID',
    title VARCHAR(500) NOT NULL DEFAULT '新对话' COMMENT '会话标题',
    extra JSON COMMENT 'JSON: {user_id, model, ...}',
    created_at BIGINT NOT NULL COMMENT '创建时间戳（Unix 毫秒）',
    updated_at BIGINT NOT NULL COMMENT '更新时间戳（Unix 毫秒）',
    deleted_at BIGINT COMMENT '软删时间戳',
    PRIMARY KEY (id),
    KEY idx_session_parent (parent_id),
    KEY idx_session_updated (updated_at),
    KEY idx_session_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='会话表 v2.1';

-- ============================================================================
-- 消息表 v2.1
-- ============================================================================
DROP TABLE IF EXISTS t_chat_message;
CREATE TABLE t_chat_message (
    id VARCHAR(36) NOT NULL COMMENT 'UUID 主键',
    session_id VARCHAR(36) NOT NULL COMMENT '所属会话 ID',
    parent_id VARCHAR(36) COMMENT '父消息 ID（追问时指向被回复的消息）',
    user_id VARCHAR(36) NOT NULL COMMENT '用户 ID',
    role TEXT NOT NULL COMMENT '角色: user | assistant',
    content JSON COMMENT '消息内容，JSON multipart 格式',
    extra JSON COMMENT 'JSON: model, tokens, finish_reason, error',
    status VARCHAR(20) NOT NULL DEFAULT 'completed' COMMENT '状态: completed | partial',
    created_at BIGINT NOT NULL COMMENT '创建时间戳（Unix 秒）',
    deleted_at BIGINT COMMENT '软删时间戳（NULL=未删除）',
    PRIMARY KEY (id),
    KEY idx_message_session (session_id, created_at),
    KEY idx_message_parent (parent_id),
    CONSTRAINT fk_message_session FOREIGN KEY (session_id) REFERENCES t_chat_session(id) ON DELETE CASCADE,
    CONSTRAINT fk_message_parent FOREIGN KEY (parent_id) REFERENCES t_chat_message(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='消息表 v2.1';
