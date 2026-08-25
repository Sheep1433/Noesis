"""Blank baseline guards before the new machine-memory pipeline is added."""

from __future__ import annotations

from pathlib import Path

from noesis.config import user_data_paths
from noesis.schemas.memory import CortexPreferenceResponse, CortexPreferenceUpdate


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = BACKEND_ROOT / "packages" / "noesis-core" / "src" / "noesis"


def test_explicit_context_files_do_not_create_daily_memory_dir(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(user_data_paths, "_USERS_ROOT", tmp_path / "users")

    root = user_data_paths.ensure_user_memory_files("user-1")

    assert (root / "USER.md").is_file()
    assert (root / "AGENTS.md").is_file()
    assert not (root / "memory").exists()


def test_single_preference_schema_has_no_secondary_switch() -> None:
    assert set(CortexPreferenceUpdate.model_fields) == {"enabled"}
    assert set(CortexPreferenceResponse.model_fields) == {"enabled"}


def test_old_memory_modules_and_runtime_wiring_are_absent() -> None:
    removed = [
        CORE_ROOT / "services" / "memory_dream_service.py",
        CORE_ROOT / "services" / "memory_dream_scheduler.py",
        CORE_ROOT / "agents" / "middlewares" / "memory_injection_middleware.py",
        CORE_ROOT / "services" / "memory" / "adapters.py",
        CORE_ROOT / "services" / "memory" / "retriever.py",
        CORE_ROOT / "services" / "memory" / "revision.py",
        CORE_ROOT / "services" / "memory" / "scheduler.py",
        CORE_ROOT / "services" / "memory" / "index_scheduler.py",
    ]
    assert not [str(path.relative_to(BACKEND_ROOT)) for path in removed if path.exists()]

    source_roots = [CORE_ROOT, BACKEND_ROOT / "server"]
    forbidden = (
        "MemoryDreamService",
        "memory_dream_scheduler",
        "MemoryInjectionMiddleware",
        "MemoryCortexConfig",
        "TMemoryExtractionJob",
        "get_user_daily_memory_path",
    )
    violations: list[str] = []
    for root in source_roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if any(token in text for token in forbidden):
                violations.append(str(path.relative_to(BACKEND_ROOT)))
    assert not violations
    assert "memory_cortex:" not in (BACKEND_ROOT / "config.yaml").read_text(
        encoding="utf-8"
    )
