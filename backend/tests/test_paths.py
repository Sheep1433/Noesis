"""paths：统一绝对路径坐标系。"""

from __future__ import annotations

import base64

import pytest

from agent.backends.docker_exec import (
    _prepare_write_file_payload,
    _session_mutex,
)
from agent.backends.paths import (
    PERSONAL_SKILLS_CONTAINER_PREFIX,
    PUBLIC_SKILLS_CONTAINER_PREFIX,
    canonicalize_agent_path,
    resolve_read_container_path,
    resolve_write_container_path,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/workspace/research/a.md", "/workspace/research/a.md"),
        ("research/a.md", "/workspace/research/a.md"),
        ("/research/a.md", "/workspace/research/a.md"),
        ("/workspace/workspace/research/a.md", "/workspace/research/a.md"),
        ("sessions/s1/workspace/research/a.md", "/workspace/research/a.md"),
        ("/sessions/s1/workspace/research/a.md", "/workspace/research/a.md"),
        ("/skills/public/x/SKILL.md", "/skills/public/x/SKILL.md"),
        ("/memory/AGENTS.md", "/memory/AGENTS.md"),
        ("/", "/workspace"),
        ("", "/workspace"),
    ],
)
def test_canonicalize_agent_path(raw: str, expected: str) -> None:
    assert canonicalize_agent_path(raw) == expected


def test_canonicalize_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="traversal"):
        canonicalize_agent_path("/workspace/../etc/passwd")


def test_resolve_read_workspace() -> None:
    path = "/workspace/research/report.md"
    assert resolve_read_container_path(path) == path


def test_resolve_read_public_skills() -> None:
    path = f"{PUBLIC_SKILLS_CONTAINER_PREFIX}/deep-research-v2/SKILL.md"
    assert resolve_read_container_path(path) == path


def test_resolve_read_personal_skills() -> None:
    path = f"{PERSONAL_SKILLS_CONTAINER_PREFIX}/my-tool/SKILL.md"
    assert resolve_read_container_path(path) == path


def test_resolve_write_rejects_skills() -> None:
    with pytest.raises(ValueError, match="read-only"):
        resolve_write_container_path(
            f"{PUBLIC_SKILLS_CONTAINER_PREFIX}/deep-research-v2/SKILL.md"
        )
    with pytest.raises(ValueError, match="read-only"):
        resolve_write_container_path(
            f"{PERSONAL_SKILLS_CONTAINER_PREFIX}/my-upload/SKILL.md"
        )


def test_resolve_read_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="traversal"):
        resolve_read_container_path("/workspace/../etc/passwd")


def test_prepare_write_file_payload_utf8() -> None:
    text, encoding = _prepare_write_file_payload("中文".encode("utf-8"))
    assert text == "中文"
    assert encoding is None


def test_prepare_write_file_payload_base64() -> None:
    raw = bytes([0xFF, 0x00, 0xFE])
    text, encoding = _prepare_write_file_payload(raw)
    assert encoding == "base64"
    assert base64.b64decode(text) == raw


def test_session_mutex_same_key() -> None:
    a = _session_mutex("u1", "s1")
    b = _session_mutex("u1", "s1")
    c = _session_mutex("u1", "s2")
    assert a is b
    assert a is not c
