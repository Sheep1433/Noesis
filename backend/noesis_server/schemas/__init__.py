"""Re-export ``noesis.schemas`` under legacy ``noesis_server.schemas`` paths.

Authoritative: ``noesis.schemas``. Submodules aliased via sys.modules.
Removed in F4.
"""
from __future__ import annotations
import sys
from importlib import import_module

_FLAT = [
    "chat_vo", "chat_attachment_vo", "knowledge_base_schema", "login_vo",
    "mcp_vo", "model_vo", "qa_vo", "session_context_vo", "settings_vo", "skill_vo",
]
for _name in _FLAT:
    _full = f"noesis_server.schemas.{_name}"
    if _full not in sys.modules:
        sys.modules[_full] = import_module(f"noesis.schemas.{_name}")
