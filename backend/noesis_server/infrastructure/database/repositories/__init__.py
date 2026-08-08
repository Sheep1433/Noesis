"""Re-export harness repositories (transition shim).

Authoritative: ``noesis.repositories``. Submodules aliased via sys.modules so
deep paths (``noesis_server.infrastructure.database.repositories.agent_run``)
keep resolving. Removed in F4.
"""
from __future__ import annotations

import sys
from importlib import import_module

_LEGACY_ALIAS = {
    "agent_run": "noesis.repositories.agent_run_repository",
    "auth": "noesis.repositories.auth_repository",
    "settings": "noesis.repositories.settings_repository",
}
for _legacy, _canonical in _LEGACY_ALIAS.items():
    sys.modules[f"noesis_server.infrastructure.database.repositories.{_legacy}"] = import_module(_canonical)
