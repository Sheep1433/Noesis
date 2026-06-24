"""sandbox-runner manager 单元测试。"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from manager import _extract_host_port, _find_free_host_port, _published_base_url


def test_published_base_url_respects_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_PUBLIC_HOST", "host.docker.internal")
    assert _published_base_url(18081) == "http://host.docker.internal:18081"


def test_find_free_host_port_returns_bindable_port() -> None:
    port = _find_free_host_port(start=39000)
    assert 39000 <= port < 41000


def test_extract_host_port_from_label() -> None:
    container = MagicMock()
    container.labels = {"noesis.host_port": "19001"}
    container.attrs = {"NetworkSettings": {"Ports": {}}}
    assert _extract_host_port(container, 8080) == 19001


def test_extract_host_port_from_docker_bindings() -> None:
    container = MagicMock()
    container.labels = {}
    container.attrs = {
        "NetworkSettings": {"Ports": {"8080/tcp": [{"HostPort": "19002"}]}}
    }
    assert _extract_host_port(container, 8080) == 19002


def test_resolve_host_data_dir_defaults_to_repo_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from paths import resolve_host_data_dir

    monkeypatch.delenv("NOESIS_HOST_DATA_DIR", raising=False)
    monkeypatch.setenv("NOESIS_REPO_ROOT", str(tmp_path))
    (tmp_path / "backend").mkdir()
    (tmp_path / "extensions").mkdir()
    assert resolve_host_data_dir() == (tmp_path / ".data").resolve()


def test_resolve_skills_host_dir_defaults_to_extensions_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from paths import resolve_skills_host_dir

    monkeypatch.delenv("SANDBOX_SKILLS_HOST_DIR", raising=False)
    monkeypatch.setenv("NOESIS_REPO_ROOT", str(tmp_path))
    (tmp_path / "backend").mkdir()
    (tmp_path / "extensions" / "skills").mkdir(parents=True)
    assert resolve_skills_host_dir() == (tmp_path / "extensions" / "skills").resolve()
