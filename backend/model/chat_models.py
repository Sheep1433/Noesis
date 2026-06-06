from typing import Optional
import time
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import VARCHAR, Integer, Text, JSON, Index, ForeignKey, BigInteger

from config.database import Base


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
    user_id: Mapped[str] = mapped_column(VARCHAR(36), nullable=False, comment='用户 ID（冗余，便于查询）')
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
    user_id: Mapped[str] = mapped_column(VARCHAR(36), nullable=False, comment='用户 ID（冗余，便于查询）')
    role: Mapped[str] = mapped_column(Text, nullable=False, comment='角色: user | assistant')
    content: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, comment='消息内容，JSON multipart 格式')
    extra: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, comment='JSON: model, tokens, finish_reason, error')
    status: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default='completed', comment='状态: completed | partial | error | streaming')
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=lambda: int(time.time() * 1000), comment='创建时间戳（Unix 毫秒）')
    deleted_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment='软删时间戳（NULL=未删除）')