"""Stable public configuration API for Noesis core.

Exports are resolved lazily because ``env`` uses runtime logging while runtime
logging uses ``paths``. Importing the subsystem itself must stay side-effect
free; concrete configuration is loaded only when a public value is requested.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    # Environment-backed, typed configuration views.
    "AppConfig": ("noesis.config.env", "AppConfig"),
    "ChatAttachmentConfig": ("noesis.config.env", "ChatAttachmentConfig"),
    "CheckpointConfig": ("noesis.config.env", "CheckpointConfig"),
    "DataBaseConfig": ("noesis.config.env", "DataBaseConfig"),
    "HitlConfig": ("noesis.config.env", "HitlConfig"),
    "KbConfig": ("noesis.config.env", "KbConfig"),
    "LangfuseConfig": ("noesis.config.env", "LangfuseConfig"),
    "MessagingConfig": ("noesis.config.env", "MessagingConfig"),
    "ModelConfig": ("noesis.config.env", "ModelConfig"),
    "OtherConfig": ("noesis.config.env", "OtherConfig"),
    "QdrantConfig": ("noesis.config.env", "QdrantConfig"),
    "SandboxConfig": ("noesis.config.env", "SandboxConfig"),
    "SessionConfig": ("noesis.config.env", "SessionConfig"),
    "SkillsMarketConfig": ("noesis.config.env", "SkillsMarketConfig"),
    "StreamConfig": ("noesis.config.env", "StreamConfig"),
    "WebToolsConfig": ("noesis.config.env", "WebToolsConfig"),
    "get_config": ("noesis.config.env", "get_config"),
    "get_sandbox_runner_token": ("noesis.config.env", "get_sandbox_runner_token"),
    "sandbox_runner_headers": ("noesis.config.env", "sandbox_runner_headers"),
    "temporary_checkpointer": ("noesis.config.checkpointer", "temporary_checkpointer"),
    # Host-independent filesystem locations.
    "BACKEND_DIR": ("noesis.config.paths", "BACKEND_DIR"),
    "DATA_DIR": ("noesis.config.paths", "DATA_DIR"),
    "REPO_ROOT": ("noesis.config.paths", "REPO_ROOT"),
    "data_path": ("noesis.config.paths", "data_path"),
    "resolve_backend_relative": ("noesis.config.paths", "resolve_backend_relative"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
