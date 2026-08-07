"""Noesis repositories — domain repositories over ``noesis.storage``.

Knowledge-base domain repositories land here in Phase C (after the KB engine
moves into ``noesis.knowledge`` in Phase B). Business-domain repositories
(``agent_run`` / ``auth`` / ``settings``) stay platform-side for now because
they depend on ``noesis_server.domain.*`` (platform delivery layer); they
consume ``noesis.storage``'s ``Base``/engine via re-export.
"""
from __future__ import annotations

__all__: list[str] = []
