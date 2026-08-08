"""Re-export ``noesis.services`` under legacy ``noesis_server.services`` paths.

Authoritative: ``noesis.services``. Submodules aliased via sys.modules so
deep paths (e.g. ``noesis.services.chat_service``) keep resolving.
Removed in F4 once api/server switch to direct noesis.services imports.
"""
from __future__ import annotations

import sys
from importlib import import_module

# Flat service modules
_FLAT = [
    "channel_operations_service", "channel_run_service", "chat_attachment_service",
    "chat_service", "hitl_timeout", "kb_collection_config_service",
    "knowledge_base_service", "login_service", "mcp_service",
    "memory_dream_scheduler", "memory_dream_service", "mention_resolve_service",
    "messaging_channel_service", "notification_preference_service",
    "run_recovery_service", "run_service", "scheduled_task_scheduler",
    "scheduled_task_service", "session_context_service",
    "settings_diagnostics_service", "settings_service", "settings_transfer_service",
    "skill_fs_service", "skill_market_service", "skills_sh_client",
    "user_memory_service", "user_service",
]
for _name in _FLAT:
    _full = f"noesis.services.{_name}"
    if _full not in sys.modules:
        try:
            sys.modules[_full] = import_module(f"noesis.services.{_name}")
        except ImportError:
            pass

# Subpackages
for _sub in ("auth", "channels", "qa"):
    _full = f"noesis.services.{_sub}"
    if _full not in sys.modules:
        try:
            sys.modules[_full] = import_module(f"noesis.services.{_sub}")
        except ImportError:
            pass
