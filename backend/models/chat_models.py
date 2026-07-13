from typing import Optional
import time
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import VARCHAR, Integer, Text, JSON, Index, ForeignKey, BigInteger, TypeDecorator

from config.database import Base


class ChatUserId(TypeDecorator):
    """聊天表用户标识的统一绑定类型。

    认证用户目前使用整数主键，而聊天表为兼容 UUID 用户标识保存字符串。
    PostgreSQL 不进行隐式转换，因此所有 ORM 写入和比较在绑定阶段统一转为字符串。
    """

    impl = VARCHAR(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ARG002
        return None if value is None else str(value)


class TChatSession(Base):
    """
    会话表 v2.1

    设计变更：
    - id: VARCHAR(36) UUID 主键（替代 BIGINT AUTO_INCREMENT）
    - parent_id: 支持会话层级（subagent 场景）
    - created_at/updated_at: BIGINT Unix 时间戳（毫秒，2026年后超过 INT 上限）
    - extra: JSON 存储 user_id、model 等
    - deleted_at: 软删时间戳，NULL 表示未删除
    """
    __tablename__ = "t_chat_session"
    __table_args__ = (
        Index('idx_session_parent', 'parent_id'),
        Index('idx_session_updated', 'updated_at'),
        Index('idx_session_user', 'user_id'),
        {'comment': '会话表 v2.1'}
    )

    id: Mapped[str] = mapped_column(VARCHAR(36), primary_key=True, comment='UUID 主键')
    parent_id: Mapped[Optional[str]] = mapped_column(VARCHAR(36), ForeignKey('t_chat_session.id', ondelete='SET NULL'), nullable=True, comment='父会话 ID（subagent 场景）')
    user_id: Mapped[str] = mapped_column(ChatUserId(), nullable=False, comment='用户 ID（冗余，便于查询）')
    title: Mapped[str] = mapped_column(VARCHAR(500), nullable=False, default='新对话', comment='会话标题')
    extra: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, comment='JSON: {user_id, model, ...}')
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=lambda: int(time.time() * 1000), comment='创建时间戳（Unix 毫秒）')
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=lambda: int(time.time() * 1000), onupdate=lambda: int(time.time() * 1000), comment='更新时间戳（Unix 毫秒）')
    deleted_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment='软删时间戳（毫秒），NULL=未删除')


class TChatMessage(Base):
    """
    消息表 v2.1

    设计变更：
    - id: VARCHAR(36) UUID 主键（替代 BIGINT AUTO_INCREMENT）
    - parent_id: 支持消息树
    - content: JSON multipart 格式（text/reasoning/tool parts）
    - status: completed / partial / error / streaming（见聊天记录 PRD；DB 列为 VARCHAR）
    - extra: 存储 model/tokens/finish_reason/error
    - user_id: 消息归属用户，便于按用户过滤与鉴权
    - 移除 sequence_num（并发冲突问题已解决）
    """
    __tablename__ = "t_chat_message"
    __table_args__ = (
        Index('idx_message_session', 'session_id', 'created_at'),
        Index('idx_message_parent', 'parent_id'),
        {'comment': '消息表 v2.1'}
    )

    id: Mapped[str] = mapped_column(VARCHAR(36), primary_key=True, comment='UUID 主键')
    session_id: Mapped[str] = mapped_column(VARCHAR(36), ForeignKey('t_chat_session.id', ondelete='CASCADE'), nullable=False, comment='所属会话 ID')
    parent_id: Mapped[Optional[str]] = mapped_column(VARCHAR(36), ForeignKey('t_chat_message.id', ondelete='SET NULL'), nullable=True, comment='父消息 ID')
    user_id: Mapped[str] = mapped_column(ChatUserId(), nullable=False, comment='用户 ID（冗余，便于查询）')
    role: Mapped[str] = mapped_column(Text, nullable=False, comment='角色: user | assistant')
    content: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, comment='消息内容，JSON multipart 格式')
    extra: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, comment='JSON: model, tokens, finish_reason, error')
    status: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default='completed', comment='状态: completed | partial | error | streaming')
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=lambda: int(time.time() * 1000), comment='创建时间戳（Unix 毫秒）')
    deleted_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment='软删时间戳（NULL=未删除）')


class TChatAttachment(Base):
    """聊天会话附件元数据（正文存磁盘，不进 BLOB）。"""
    __tablename__ = "t_chat_attachment"
    __table_args__ = (
        Index('idx_attachment_session', 'session_id', 'created_at'),
        Index('idx_attachment_expires', 'expires_at'),
        {'comment': '聊天会话附件表'}
    )

    id: Mapped[str] = mapped_column(VARCHAR(36), primary_key=True, comment='UUID attachment_id')
    session_id: Mapped[str] = mapped_column(
        VARCHAR(36), ForeignKey('t_chat_session.id', ondelete='CASCADE'), nullable=False, comment='所属会话'
    )
    user_id: Mapped[str] = mapped_column(ChatUserId(), nullable=False, comment='用户 ID')
    file_name: Mapped[str] = mapped_column(VARCHAR(500), nullable=False, comment='原始文件名')
    kind: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, comment='document | image')
    original_path: Mapped[str] = mapped_column(VARCHAR(1000), nullable=False, comment='原文件相对路径')
    markdown_path: Mapped[Optional[str]] = mapped_column(
        VARCHAR(1000), nullable=True, comment='解析后 Markdown 相对路径'
    )
    mime_type: Mapped[Optional[str]] = mapped_column(VARCHAR(100), nullable=True, comment='MIME 类型')
    virtual_path: Mapped[str] = mapped_column(VARCHAR(1000), nullable=False, comment='Agent 工具逻辑路径')
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment='Markdown 字符数')
    status: Mapped[str] = mapped_column(
        VARCHAR(20), nullable=False, default='uploaded', comment='uploaded | parsed | failed'
    )
    preview_base64: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment='图片缩略图 base64（可选）')
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='创建时间戳（毫秒）')
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='过期时间戳（毫秒）')
