"""Re-export ``noesis.knowledge.implementations.qdrant`` (transition shim)."""
from __future__ import annotations

from noesis.knowledge.implementations.qdrant import *  # noqa: F401,F403
from noesis.knowledge.implementations.qdrant import (  # noqa: F401
    QdrantService,
    close_qdrant_client,
    get_qdrant_client,
    init_qdrant_client,
    is_qdrant_connected,
)
