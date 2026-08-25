"""全量 ORM model 注册入口。

importing this module ensures all ORM models are registered with
``Base.metadata`` so Alembic autogenerate and ``create_all`` detect every
table.
"""
from __future__ import annotations

from noesis.storage.postgres.models.auth import TUser, TUserSession
from noesis.storage.postgres.models.bg_task import TBackgroundTask
from noesis.storage.postgres.models.chat import (
    TAgentDelivery,
    TAgentRun,
    TChatAttachment,
    TChatMessage,
    TChatSession,
)
from noesis.storage.postgres.models.knowledge import TKbCollectionConfig
from noesis.storage.postgres.models.memory import (
    TMemoryEvidence,
    TMemoryItem,
    TMemoryJob,
    TMemoryOutbox,
    TMemoryQueryTrace,
    TMemoryRelation,
    TMemoryRunSnapshot,
    TMemoryUserPreference,
)
from noesis.storage.postgres.models.scheduled_task import TUserScheduledTask
from noesis.storage.postgres.models.user_llm import (
    TUserLLMModel,
    TUserLLMPreference,
    TUserLLMProvider,
)
from noesis.storage.postgres.models.settings import (
    TUserNotificationPreference,
    TUserSettingsAudit,
    TUserScheduledTaskRun,
)

__all__ = [
    "TUser",
    "TUserLLMProvider",
    "TUserLLMModel",
    "TUserLLMPreference",
    "TUserSession",
    "TBackgroundTask",
    "TChatSession",
    "TChatMessage",
    "TAgentRun",
    "TAgentDelivery",
    "TChatAttachment",
    "TKbCollectionConfig",
    "TMemoryEvidence",
    "TMemoryItem",
    "TMemoryJob",
    "TMemoryOutbox",
    "TMemoryQueryTrace",
    "TMemoryRelation",
    "TMemoryRunSnapshot",
    "TMemoryUserPreference",
    "TUserScheduledTask",
    "TUserScheduledTaskRun",
    "TUserNotificationPreference",
    "TUserSettingsAudit",
]
