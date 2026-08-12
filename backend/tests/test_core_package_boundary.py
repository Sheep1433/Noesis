"""Architecture contract for the installable Noesis core package."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib


BACKEND_ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGE_ROOT = BACKEND_ROOT / "packages" / "noesis-core"
NOESIS_ROOT = CORE_PACKAGE_ROOT / "src" / "noesis"
FORBIDDEN_PLATFORM_PACKAGES = {
    "api",
    "common",
    "config",
    "domain",
    "kb",
    "models",
    "services",
    "server",
}
LEGACY_TOP_LEVEL_PACKAGES = {"agent", "harness", "llm"}
LEGACY_PLATFORM_PACKAGES = {
    "api", "common", "config", "constants", "domain", "exceptions",
    "kb", "middleware", "models", "schemas", "services",
}


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def test_core_does_not_import_server_layers() -> None:
    violations: list[str] = []
    for path in sorted(NOESIS_ROOT.rglob("*.py")):
        if "_ragflow_compat" in path.parts or "deepdoc" in path.parts:
            continue
        # noesis.services may import fastapi (Request/Depends for auth) —
        # YuXi's services also use fastapi directly. fastapi is NOT in the
        # forbidden set (only server.* platform packages are).
        forbidden = _top_level_imports(path) & FORBIDDEN_PLATFORM_PACKAGES
        if forbidden:
            relative = path.relative_to(BACKEND_ROOT)
            violations.append(f"{relative}: {', '.join(sorted(forbidden))}")

    assert not violations, "core package imports server layers:\n" + "\n".join(violations)


def test_core_never_imports_server_namespace() -> None:
    violations: list[str] = []
    for path in sorted(NOESIS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        modules.extend(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        if any(name == "server" or name.startswith("server.") for name in modules):
            violations.append(str(path.relative_to(BACKEND_ROOT)))

    assert not violations, "core package imports server namespace:\n" + "\n".join(violations)


def test_backend_has_no_legacy_imports_or_shims() -> None:
    legacy_imports: list[str] = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if (
            ".venv" in path.parts
            or "datasets" in path.parts
            or "_ragflow_compat" in path.parts
            or "deepdoc" in path.parts
        ):
            continue
        if _top_level_imports(path) & LEGACY_TOP_LEVEL_PACKAGES:
            legacy_imports.append(str(path.relative_to(BACKEND_ROOT)))

    assert not legacy_imports, "legacy top-level imports remain:\n" + "\n".join(legacy_imports)
    assert not (BACKEND_ROOT / "agent").exists(), "backend/agent shim must not remain"
    assert not (BACKEND_ROOT / "packages" / "harness").exists()
    assert not (BACKEND_ROOT / "packages" / "llm").exists()


def test_core_distribution_owns_noesis_and_llm_packages() -> None:
    core_project = tomllib.loads(
        (CORE_PACKAGE_ROOT / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    dependencies = core_project["project"]["dependencies"]
    assert core_project["project"]["name"] == "noesis-core"
    assert "noesis-llm" not in dependencies
    for direct_dependency in (
        "alembic",
        "asyncpg",
        "jieba",
        "langchain-anthropic",
        "langchain-text-splitters",
        "markitdown",
        "openai",
        "psycopg[binary,pool]",
        "python-docx",
        "qdrant-client",
        "sqlalchemy[asyncio]",
    ):
        assert any(
            dependency.startswith(direct_dependency)
            for dependency in dependencies
        ), direct_dependency
    assert core_project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/noesis"
    ]
    assert (NOESIS_ROOT / "llm" / "factory.py").is_file()


def test_noesis_top_level_uses_stable_subsystems() -> None:
    for legacy_dir in ("attachments", "case_generate", "profiles"):
        assert not (NOESIS_ROOT / legacy_dir).exists()
    for scattered_runtime_file in ("deps.py", "hitl.py", "logging.py", "stream.py"):
        assert not (NOESIS_ROOT / scattered_runtime_file).exists()

    assert (NOESIS_ROOT / "agents" / "case_generate" / "case_graph.py").is_file()
    assert (NOESIS_ROOT / "runtime" / "attachments" / "resolver.py").is_file()
    assert (NOESIS_ROOT / "runtime" / "stream.py").is_file()


def test_platform_host_uses_single_namespace() -> None:
    platform_root = BACKEND_ROOT / "server"
    assert (platform_root / "main.py").is_file()
    for package in LEGACY_PLATFORM_PACKAGES:
        assert not (BACKEND_ROOT / package).exists(), f"legacy platform package remains: {package}"

    violations: list[str] = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if (
            ".venv" in path.parts
            or "datasets" in path.parts
            or "_ragflow_compat" in path.parts
            or "deepdoc" in path.parts
        ):
            continue
        legacy = _top_level_imports(path) & LEGACY_PLATFORM_PACKAGES
        if legacy:
            violations.append(f"{path.relative_to(BACKEND_ROOT)}: {', '.join(sorted(legacy))}")
    assert not violations, "legacy platform imports remain:\n" + "\n".join(violations)


def test_platform_core_does_not_depend_on_application_services() -> None:
    # chat/auth live in core (noesis.chat, noesis.auth); assert they do not import
    # application services (noesis.services) — one-way dependency.
    domain_roots = [NOESIS_ROOT / "chat", NOESIS_ROOT / "auth"]
    violations: list[str] = []
    for domain_root in domain_roots:
        for path in sorted(domain_root.rglob("*.py")):
            if "_ragflow_compat" in path.parts or "deepdoc" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            modules = [
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            ]
            modules.extend(
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            )
            if any(
                name == prefix or name.startswith(prefix + ".")
                for name in modules
                for prefix in ("noesis.services", "noesis.agents")
            ):
                violations.append(str(path.relative_to(BACKEND_ROOT)))
    assert not violations, "noesis.chat/auth imports services/agents:\n" + "\n".join(violations)

    # platform common may import core (noesis.*) but must not import
    # platform services/domain/kb top-level packages.
    platform_root = BACKEND_ROOT / "server"
    for path in sorted((platform_root / "common").rglob("*.py")):
        assert not (_top_level_imports(path) & {"services", "domain", "kb"}), path


def test_knowledge_base_api_uses_application_service() -> None:
    path = BACKEND_ROOT / "server" / "api" / "knowledge_base_api.py"
    source = path.read_text(encoding="utf-8")
    # API uses core service (noesis.services), not platform server.services
    assert "noesis.services import knowledge_base_service" in source or "noesis.services.knowledge_base_service" in source
    assert "server.kb" not in source


def test_factory_import_needs_no_platform_startup_or_wiring() -> None:
    env = os.environ.copy()
    source_roots = [
        str(CORE_PACKAGE_ROOT / "src"),
        str(BACKEND_ROOT),
    ]
    if env.get("PYTHONPATH"):
        source_roots.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(source_roots)

    script = """
import sys
from noesis.factory import create_noesis_agent

assert callable(create_noesis_agent)
for prefix in ("api", "domain", "kb", "models", "services", "server"):
    assert not any(name == prefix or name.startswith(prefix + ".") for name in sys.modules), prefix
assert "fastapi" not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_public_subsystem_facades_are_lazy_and_authoritative() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(CORE_PACKAGE_ROOT / "src"), str(BACKEND_ROOT)]
    )
    script = """
import sys
import noesis.config as config
import noesis.runtime as runtime

assert "noesis.config.env" not in sys.modules
assert "noesis.runtime.stream" not in sys.modules
assert "noesis.runtime.deps" not in sys.modules

from noesis.config import ModelConfig, data_path
from noesis.config.env import ModelConfig as direct_model_config
from noesis.config.paths import data_path as direct_data_path
from noesis.runtime import logger, stream_agent_events
from noesis.runtime.logging import logger as direct_logger
from noesis.runtime.stream import stream_agent_events as direct_stream

assert ModelConfig is direct_model_config
assert data_path is direct_data_path
assert logger is direct_logger
assert stream_agent_events is direct_stream
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_eval_runtime_imports_no_platform_services() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(CORE_PACKAGE_ROOT / "src"), str(BACKEND_ROOT)]
    )
    script = """
import asyncio
import sys
from evals.bootstrap import eval_runtime

async def main():
    async with eval_runtime():
        assert not any(
            name == "server.services" or name.startswith("noesis.services.")
            for name in sys.modules
        )

asyncio.run(main())
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_built_wheel_imports_outside_backend(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise AssertionError("uv is required for the core wheel smoke test")

    dist = tmp_path / "dist"
    subprocess.run(
        [uv, "build", str(CORE_PACKAGE_ROOT), "--wheel", "--out-dir", str(dist)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist.glob("noesis_core-*.whl"))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(wheel)
    env["NOESIS_DATA_DIR"] = str(tmp_path / "data")
    script = """
import noesis
import sys
from importlib.resources import files
from noesis.factory import create_noesis_agent
from noesis.llm import get_llm
from noesis.config import ModelConfig, data_path
from noesis.runtime import logger, stream_agent_events
from noesis.knowledge import KnowledgeBase, KnowledgeBaseFactory, KnowledgeBaseManager
from noesis.knowledge.parser.parser import DocumentParser
from noesis.knowledge.retrieval.store import kb_bm25_preprocess
from noesis.repositories import AgentRunRepository
from noesis.runtime.attachments.vlm_caption import describe_image_bytes_for_chat
from noesis.storage.postgres.base import Base
from noesis.services import chat_service
from noesis.schemas.chat_vo import CreateRunRequest

assert ".whl/" in noesis.__file__, noesis.__file__
assert callable(create_noesis_agent)
assert callable(get_llm)
assert ModelConfig.model_name
assert callable(data_path)
assert callable(stream_agent_events)
assert logger is not None
assert KnowledgeBase is not None
assert KnowledgeBaseFactory is not None
assert KnowledgeBaseManager is not None
assert DocumentParser is not None
assert kb_bm25_preprocess("中文检索")
assert callable(describe_image_bytes_for_chat)
assert AgentRunRepository is not None
assert Base is not None
assert chat_service is not None
assert CreateRunRequest is not None
assert files("noesis.storage.migrations").joinpath("alembic.ini").is_file()
for prefix in ("common", "config", "services"):
    assert not any(name == prefix or name.startswith(prefix + ".") for name in sys.modules)
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
