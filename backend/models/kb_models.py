"""知识库集合配置模型。"""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, JSON, String, text
from sqlalchemy.orm import Mapped, mapped_column

from config.database import Base


class TKbCollectionConfig(Base):
    """集合级 processing_params / query_params（向量数据仍在 Qdrant）。"""

    __tablename__ = "kb_collection_config"

    collection_name: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
        comment="与 Qdrant collection 同名",
    )
    processing_params: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True,
        comment="入库默认参数",
    )
    query_params: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True,
        comment="检索默认参数",
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
        comment="创建时间",
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.datetime.utcnow,
        nullable=False,
        comment="更新时间",
    )
