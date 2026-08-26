"""聊天/Run 域 ORM：会话、消息、Agent run、投递、附件。"""
from typing import Optional
import time
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import VARCHAR, Integer, Text, JSON, Index, ForeignKey, BigInteger, Boolean, Uuid, UniqueConstraint, text as sa_text

from noesis.storage.postgres.base import Base


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
        Index('idx_session_user_archived', 'user_id', 'archived'),
        {'comment': '会话表 v2.1'}
    )

    id: Mapped[str] = mapped_column(VARCHAR(36), primary_key=True, comment='UUID 主键')
    parent_id: Mapped[Optional[str]] = mapped_column(VARCHAR(36), ForeignKey('t_chat_session.id', ondelete='SET NULL'), nullable=True, comment='父会话 ID（subagent 场景）')
    kind: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default='root', server_default='root', comment='会话类型: root | subagent')
    created_by_run_id: Mapped[Optional[str]] = mapped_column(VARCHAR(36), ForeignKey('t_agent_run.id', ondelete='SET NULL'), nullable=True, comment='创建该子会话的 Agent run ID')
    created_by_tool_call_id: Mapped[Optional[str]] = mapped_column(VARCHAR(128), nullable=True, comment='创建该子会话的工具调用 ID')
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, comment='用户 UUID（冗余，便于查询）')
    title: Mapped[str] = mapped_column(VARCHAR(500), nullable=False, default='新对话', comment='会话标题')
    extra: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, comment='JSON: {user_id, model, ...}')
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=lambda: int(time.time() * 1000), comment='创建时间戳（Unix 毫秒）')
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=lambda: int(time.time() * 1000), onupdate=lambda: int(time.time() * 1000), comment='更新时间戳（Unix 毫秒）')
    deleted_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment='软删时间戳（毫秒），NULL=未删除')
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_text("false"), comment='是否置顶（置顶会话排到列表顶部）')
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sa_text("false"), comment='是否归档（归档会话从默认列表隐藏）')
    next_message_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default=sa_text("1"), comment='下一条会话消息序号')
    memory_extracted_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment='记忆抽取完成时间戳（毫秒），NULL=未抽取；见 md-memory-layer')
    memory_extracted_seq: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment='记忆抽取水位：已成功抽取的最大消息序号；NULL=从未抽取')
    last_read_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment='用户最后查看会话的时间戳（毫秒），NULL=从未读过；updated_at > last_read_at 表示有未读')


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
        Index('idx_message_session', 'session_id', 'message_sequence'),
        Index('idx_message_parent', 'parent_id'),
        UniqueConstraint('session_id', 'message_sequence', name='uq_chat_message_session_sequence'),
        {'comment': '消息表 v2.1'}
    )

    id: Mapped[str] = mapped_column(VARCHAR(36), primary_key=True, comment='UUID 主键')
    session_id: Mapped[str] = mapped_column(VARCHAR(36), ForeignKey('t_chat_session.id', ondelete='CASCADE'), nullable=False, comment='所属会话 ID')
    parent_id: Mapped[Optional[str]] = mapped_column(VARCHAR(36), ForeignKey('t_chat_message.id', ondelete='SET NULL'), nullable=True, comment='父消息 ID')
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, comment='用户 UUID（冗余，便于查询）')
    role: Mapped[str] = mapped_column(Text, nullable=False, comment='角色: user | assistant')
    content: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, comment='消息内容，JSON multipart 格式')
    extra: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, comment='JSON: model, tokens, finish_reason, error')
    status: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default='completed', comment='状态: completed | partial | error | streaming')
    message_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, comment='会话内严格递增的消息序号')
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=lambda: int(time.time() * 1000), comment='创建时间戳（Unix 毫秒）')
    deleted_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment='软删时间戳（NULL=未删除）')


class TAgentRun(Base):
    """可查询、可重订阅的 Agent run 元数据。"""

    __tablename__ = "t_agent_run"
    __table_args__ = (
        UniqueConstraint("user_id", "client_request_id", name="uq_agent_run_user_request"),
        Index("idx_agent_run_session_status", "session_id", "status"),
        Index(
            "uq_agent_run_active_session",
            "session_id",
            unique=True,
            postgresql_where=sa_text("status IN ('queued','running','retrying','hitl_pending')"),
        ),
        Index("idx_agent_run_owner_status", "owner_instance_id", "status"),
        Index("idx_agent_run_updated", "updated_at"),
        {"comment": "Agent run 生命周期与恢复快照"},
    )

    id: Mapped[str] = mapped_column(VARCHAR(36), primary_key=True, comment="run UUID")
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, comment="用户 UUID")
    session_id: Mapped[str] = mapped_column(
        VARCHAR(36), ForeignKey("t_chat_session.id", ondelete="CASCADE"), nullable=False
    )
    assistant_message_id: Mapped[str] = mapped_column(
        VARCHAR(36), ForeignKey("t_chat_message.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    client_request_id: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    qa_type: Mapped[str] = mapped_column(VARCHAR(40), nullable=False)
    origin: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="web")
    status: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="queued")
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    attempt_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    finish_reason: Mapped[Optional[str]] = mapped_column(VARCHAR(40), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(VARCHAR(80), nullable=True)
    user_error_message: Mapped[Optional[str]] = mapped_column(VARCHAR(500), nullable=True)
    owner_instance_id: Mapped[Optional[str]] = mapped_column(VARCHAR(100), nullable=True)
    snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    memory_context: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="Run-private recalled memory ids/hash"
    )
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finished_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class TAgentDelivery(Base):
    """run 到外部平台的一次出站结果；不参与 Agent 终态。"""

    __tablename__ = "t_agent_delivery"
    __table_args__ = (
        Index("idx_agent_delivery_run", "run_id", "created_at"),
        Index("idx_agent_delivery_status", "status", "updated_at"),
        {"comment": "Agent run 外部平台投递结果"},
    )

    id: Mapped[str] = mapped_column(VARCHAR(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        VARCHAR(36), ForeignKey("t_agent_run.id", ondelete="CASCADE"), nullable=False
    )
    delivery_type: Mapped[str] = mapped_column(VARCHAR(30), nullable=False)
    status: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="running")
    error_code: Mapped[Optional[str]] = mapped_column(VARCHAR(80), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(VARCHAR(500), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finished_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


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
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, comment='用户 UUID')
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
