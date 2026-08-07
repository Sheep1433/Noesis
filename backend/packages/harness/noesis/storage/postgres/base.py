"""单一 ORM Base，全量 model 共用。

从 ``noesis_server/infrastructure/database/engine.py`` 迁入；所有 ORM model
继承同一 ``Base``，保证 ``Base.metadata`` 完整以供 ``create_all`` 与 Alembic
autogenerate。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase


class Base(AsyncAttrs, DeclarativeBase):
    pass
