"""Architecture contract for the extracted Agent harness workspace package."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib


BACKEND_ROOT = Path(__file__).resolve().parents[1]
NOESIS_ROOT = BACKEND_ROOT / "packages" / "harness" / "noesis"
FORBIDDEN_PLATFORM_PACKAGES = {
    "api",
    "common",
    "config",
    "domain",
    "kb",
    "models",
    "services",
    "noesis_server",
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


def test_harness_does_not_import_platform_layers() -> None:
    violations: list[str] = []
    for path in sorted(NOESIS_ROOT.rglob("*.py")):
        if "_ragflow_compat" in path.parts or "deepdoc" in path.parts:
            continue
        forbidden = _top_level_imports(path) & FORBIDDEN_PLATFORM_PACKAGES
        if forbidden:
            relative = path.relative_to(BACKEND_ROOT)
            violations.append(f"{relative}: {', '.join(sorted(forbidden))}")

    assert not violations, "harness imports platform layers:\n" + "\n".join(violations)


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
    assert not (BACKEND_ROOT / "packages" / "harness" / "harness").exists()
    assert not (BACKEND_ROOT / "packages" / "llm").exists()


def test_harness_owns_noesis_and_llm_packages() -> None:
    harness_project = tomllib.loads(
        (BACKEND_ROOT / "packages" / "harness" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    dependencies = harness_project["project"]["dependencies"]
    assert "noesis-llm" not in dependencies
    assert harness_project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "noesis"
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
    platform_root = BACKEND_ROOT / "noesis_server"
    assert (platform_root / "server.py").is_file()
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
    # domain now lives in harness (noesis.domain); assert it does not import
    # application services (noesis.services) — one-way dependency.
    domain_root = NOESIS_ROOT / "domain"
    violations: list[str] = []
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
        if any(name == "noesis.services" or name.startswith("noesis.services.") for name in modules):
            violations.append(str(path.relative_to(BACKEND_ROOT)))
    assert not violations, "noesis.domain imports application services:\n" + "\n".join(violations)

    # platform common may re-export harness (noesis.*) but must not import
    # platform services/domain/kb top-level packages.
    platform_root = BACKEND_ROOT / "noesis_server"
    for path in sorted((platform_root / "common").rglob("*.py")):
        assert not (_top_level_imports(path) & {"services", "domain", "kb"}), path


def test_knowledge_base_api_uses_application_service() -> None:
    path = BACKEND_ROOT / "noesis_server" / "api" / "knowledge_base_api.py"
    source = path.read_text(encoding="utf-8")
    assert "noesis_server.services import knowledge_base_service" in source
    assert "noesis_server.kb" not in source
    assert "kb_collection_config_service" not in source


def test_factory_import_needs_no_platform_startup_or_wiring() -> None:
    env = os.environ.copy()
    source_roots = [
        str(BACKEND_ROOT / "packages" / "harness"),
        str(BACKEND_ROOT),
    ]
    if env.get("PYTHONPATH"):
        source_roots.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(source_roots)

    script = """
import sys
from noesis.factory import create_noesis_agent

assert callable(create_noesis_agent)
for prefix in ("api", "domain", "kb", "models", "services", "noesis_server"):
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
        [str(BACKEND_ROOT / "packages" / "harness"), str(BACKEND_ROOT)]
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
        [str(BACKEND_ROOT / "packages" / "harness"), str(BACKEND_ROOT)]
    )
    script = """
import asyncio
import sys
from evals.bootstrap import eval_runtime

async def main():
    async with eval_runtime():
        assert not any(
            name == "noesis_server.services" or name.startswith("noesis_server.services.")
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
        raise AssertionError("uv is required for the harness wheel smoke test")

    dist = tmp_path / "dist"
    subprocess.run(
        [uv, "build", str(BACKEND_ROOT / "packages" / "harness"), "--wheel", "--out-dir", str(dist)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist.glob("noesis_harness-*.whl"))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(wheel)
    env["NOESIS_DATA_DIR"] = str(tmp_path / "data")
    script = """
import noesis
import sys
from noesis.factory import create_noesis_agent
from noesis.llm import get_llm
from noesis.config import ModelConfig, data_path
from noesis.runtime import logger, stream_agent_events

assert ".whl/" in noesis.__file__, noesis.__file__
assert callable(create_noesis_agent)
assert callable(get_llm)
assert ModelConfig.model_name
assert callable(data_path)
assert callable(stream_agent_events)
assert logger is not None
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
