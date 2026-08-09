"""全量 ORM model 注册入口。

importing this module ensures all ORM models are registered with
``Base.metadata`` so Alembic autogenerate and ``create_all`` detect every
table.
"""
from __future__ import annotations

from noesis.storage.postgres.models.auth import TUser, TUserSession
from noesis.storage.postgres.models.chat import (
    TAgentDelivery,
    TAgentRun,
    TChatAttachment,
    TChatMessage,
    TChatSession,
)
from noesis.storage.postgres.models.knowledge import TKbCollectionConfig
from noesis.storage.postgres.models.scheduled_task import TUserScheduledTask
from noesis.storage.postgres.models.settings import (
    TUserNotificationPreference,
    TUserSettingsAudit,
    TUserScheduledTaskRun,
)

__all__ = [
    "TUser",
    "TUserSession",
    "TChatSession",
    "TChatMessage",
    "TAgentRun",
    "TAgentDelivery",
    "TChatAttachment",
    "TKbCollectionConfig",
    "TUserScheduledTask",
    "TUserScheduledTaskRun",
    "TUserNotificationPreference",
    "TUserSettingsAudit",
]
