from typing import Optional
import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import VARCHAR, DateTime, Integer, BigInteger, Index
from config.database import Base


class TUser(Base):
    __tablename__ = "t_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[Optional[str]] = mapped_column(VARCHAR(200), unique=True, comment="用户名称")
    password: Mapped[Optional[str]] = mapped_column(VARCHAR(300), comment="密码")
    mobile: Mapped[Optional[str]] = mapped_column(VARCHAR(100), comment="手机号")
    registration_invite_digest: Mapped[Optional[str]] = mapped_column(
        VARCHAR(64), unique=True, comment="当前全局注册码 SHA-256 摘要"
    )
    registration_invite_updated_at: Mapped[Optional[int]] = mapped_column(
        BigInteger, comment="当前全局注册码轮换时间（毫秒）"
    )
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment="创建时间")
    update_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment="修改时间")


class TUserSession(Base):
    __tablename__ = "t_user_session"
    __table_args__ = (
        Index("idx_user_session_user_active", "user_id", "revoked_at", "idle_expires_at"),
    )

    id: Mapped[str] = mapped_column(VARCHAR(36), primary_key=True, comment="会话公开标识")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="用户 ID")
    session_digest: Mapped[str] = mapped_column(VARCHAR(64), unique=True, nullable=False, comment="Session SHA-256 摘要")
    csrf_digest: Mapped[str] = mapped_column(VARCHAR(64), nullable=False, comment="CSRF Token SHA-256 摘要")
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="创建时间（毫秒）")
    last_seen_at: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="最近活跃时间（毫秒）")
    idle_expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="空闲过期时间（毫秒）")
    absolute_expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="绝对过期时间（毫秒）")
    revoked_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="撤销时间（毫秒）")
    device_name: Mapped[Optional[str]] = mapped_column(VARCHAR(200), nullable=True, comment="设备名称")
    user_agent_digest: Mapped[Optional[str]] = mapped_column(VARCHAR(64), nullable=True, comment="User-Agent 摘要")
    last_ip: Mapped[Optional[str]] = mapped_column(VARCHAR(64), nullable=True, comment="最近客户端 IP")

