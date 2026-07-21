"""sandbox_mount_policy 与 sandbox_common 单元测试。"""

from __future__ import annotations

import base64

import pytest

from agent.backends.mount_paths import (
    PERSONAL_SKILLS_CONTAINER_PREFIX,
    PUBLIC_SKILLS_CONTAINER_PREFIX,
)
from agent.backends.sandbox_common import prepare_write_file_payload, session_mutex
from agent.backends.sandbox_mount_policy import (
    resolve_read_container_path,
    resolve_write_container_path,
)


def test_resolve_read_workspace() -> None:
    path = "/workspace/research/report.md"
    assert resolve_read_container_path(path) == path


def test_resolve_read_public_skills_mount() -> None:
    path = f"{PUBLIC_SKILLS_CONTAINER_PREFIX}/deep-research-v2/SKILL.md"
    assert resolve_read_container_path(path) == path


def test_resolve_read_personal_skills_mount() -> None:
    path = f"{PERSONAL_SKILLS_CONTAINER_PREFIX}/my-tool/SKILL.md"
    assert resolve_read_container_path(path) == path


def test_resolve_read_rejects_non_absolute() -> None:
    with pytest.raises(ValueError, match="absolute"):
        resolve_read_container_path("research/report.md")


def test_resolve_write_blocks_public_skills() -> None:
    with pytest.raises(ValueError, match="read-only"):
        resolve_write_container_path(
            f"{PUBLIC_SKILLS_CONTAINER_PREFIX}/deep-research-v2/SKILL.md"
        )


def test_resolve_write_blocks_personal_skills() -> None:
    with pytest.raises(ValueError, match="read-only"):
        resolve_write_container_path(
            f"{PERSONAL_SKILLS_CONTAINER_PREFIX}/my-upload/SKILL.md"
        )


def test_resolve_read_blocks_traversal() -> None:
    with pytest.raises(ValueError, match="traversal"):
        resolve_read_container_path("/workspace/../etc/passwd")


def test_prepare_write_file_payload_utf8() -> None:
    text, encoding = prepare_write_file_payload("中文".encode("utf-8"))
    assert text == "中文"
    assert encoding is None


def test_prepare_write_file_payload_base64() -> None:
    raw = bytes(range(256))
    text, encoding = prepare_write_file_payload(raw)
    assert encoding == "base64"
    assert base64.b64decode(text) == raw


def test_session_mutex_per_session() -> None:
    assert session_mutex("u1", "s1") is not session_mutex("u1", "s2")
