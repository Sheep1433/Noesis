"""用户自定义对话模型 ORM：provider（连接+密钥）与 model（模型条目）。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from noesis.storage.postgres.base import Base


class TUserLLMProvider(Base):
    __tablename__ = "user_llm_providers"
    __table_args__ = (
        Index("idx_user_llm_providers_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="Provider ID")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="用户 ID")
    name: Mapped[str] = mapped_column(String(120), nullable=False, comment="显示名")
    api_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="协议类型：openai/deepseek/qwen/minimax/opencode")
    base_url: Mapped[str] = mapped_column(String(500), nullable=False, comment="OpenAI 兼容端点")
    api_key_cipher: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="加密后的 API Key（enc: 前缀）")
    api_key_suffix: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, comment="Key 尾部明文片段，仅用于展示")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="软删除时间（毫秒）")
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TUserLLMModel(Base):
    __tablename__ = "user_llm_models"
    __table_args__ = (
        Index("idx_user_llm_models_user", "user_id"),
        Index("uq_user_llm_models_user_model", "user_id", "model_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="条目 ID")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, comment="用户 ID")
    provider_id: Mapped[str] = mapped_column(String(36), ForeignKey("user_llm_providers.id"), nullable=False, comment="所属 Provider")
    model_id: Mapped[str] = mapped_column(String(200), nullable=False, comment="发送给端点的模型 ID")
    label: Mapped[str] = mapped_column(String(200), nullable=False, comment="选择器显示名")
    temperature: Mapped[Optional[float]] = mapped_column(nullable=True, comment="可选温度覆盖")
    context_window: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="上下文窗口（token）")
    deleted_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, comment="软删除时间（毫秒）")
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
