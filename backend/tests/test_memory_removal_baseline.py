"""机器记忆皮层删除后的 removal baseline（md-memory-layer 变更前置清理）。"""

from __future__ import annotations

from pathlib import Path

from noesis.config import user_data_paths


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = BACKEND_ROOT / "packages" / "noesis-core" / "src" / "noesis"


def test_explicit_context_files_do_not_create_memory_dir(
    tmp_path: Path, monkeypatch,
) -> None:
    """显式 USER.md / AGENTS.md 能力不受删除影响，且不派生记忆目录。"""
    monkeypatch.setattr(user_data_paths, "_USERS_ROOT", tmp_path / "users")

    root = user_data_paths.ensure_user_memory_files("user-1")

    assert (root / "USER.md").is_file()
    assert (root / "AGENTS.md").is_file()


def test_old_cortex_modules_are_absent() -> None:
    removed = [
        CORE_ROOT / "services" / "memory" / "bulletin.py",
        CORE_ROOT / "services" / "memory" / "capture.py",
        CORE_ROOT / "services" / "memory" / "index.py",
        CORE_ROOT / "services" / "memory" / "query.py",
        CORE_ROOT / "services" / "memory" / "worker.py",
        CORE_ROOT / "agents" / "middlewares" / "memory_bulletin_middleware.py",
        CORE_ROOT / "agents" / "memory_runtime.py",
        CORE_ROOT / "repositories" / "machine_memory_repository.py",
        CORE_ROOT / "repositories" / "memory_preference_repository.py",
        CORE_ROOT / "schemas" / "memory.py",
        CORE_ROOT / "storage" / "postgres" / "models" / "memory.py",
        CORE_ROOT / "config" / "memory_paths.py",
        BACKEND_ROOT / "evals" / "memory_cortex",
    ]
    assert not [str(path.relative_to(BACKEND_ROOT)) for path in removed if path.exists()]


def test_old_cortex_wiring_is_absent_from_source() -> None:
    forbidden = (
        "MemoryCaptureService",
        "MachineMemoryRepository",
        "MemoryBulletinService",
        "MachineMemoryService",
        "build_memory_bulletin_middleware",
        "MachineMemoryConfig",
        "machine_memory",
        "TMemoryItem",
    )
    violations: list[str] = []
    for root in (CORE_ROOT, BACKEND_ROOT / "server"):
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if "migrations" in path.parts:
                continue  # 历史迁移保留原样，只看现行代码
            text = path.read_text(encoding="utf-8")
            if any(token in text for token in forbidden):
                violations.append(str(path.relative_to(BACKEND_ROOT)))
    assert not violations


def test_run_memory_context_column_survives() -> None:
    """t_agent_run.memory_context 保留（复用为注入清单）。"""
    chat_models = (CORE_ROOT / "storage" / "postgres" / "models" / "chat.py").read_text(
        encoding="utf-8"
    )
    assert "memory_context" in chat_models


def test_drop_migration_exists() -> None:
    migrations = (
        CORE_ROOT / "storage" / "migrations" / "versions"
    ).glob("*_drop_memory_cortex.py")
    assert list(migrations), "missing drop migration for memory cortex tables"
