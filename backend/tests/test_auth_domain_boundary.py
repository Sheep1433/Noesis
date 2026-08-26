"""Architecture and pure-rule tests for the authentication domain."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from noesis.auth.entities import AuthSession, AuthUser
from noesis.auth.policy import (
    digest_secret,
    is_session_valid,
    session_expiry,
    touch_session,
    verify_csrf,
    verify_invite_digest,
)
from noesis.repositories.auth_repository import (
    SqlAlchemySessionRepository,
    SqlAlchemyUserRepository,
    session_from_orm,
    user_from_orm,
)
from noesis.storage.postgres.models.auth import TUser, TUserSession


BACKEND_ROOT = Path(__file__).resolve().parents[1]
AUTH_DOMAIN = BACKEND_ROOT / "server" / "domain" / "auth"
FORBIDDEN = {
    "fastapi",
    "sqlalchemy",
    "server.exceptions",
    "server.infrastructure",
    "server.models",
    "server.services",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_auth_domain_has_no_framework_or_persistence_imports() -> None:
    violations: list[str] = []
    for path in sorted(AUTH_DOMAIN.glob("*.py")):
        for module in _imports(path):
            if any(module == prefix or module.startswith(prefix + ".") for prefix in FORBIDDEN):
                violations.append(f"{path.name}: {module}")
    assert not violations, "auth domain boundary violations:\n" + "\n".join(violations)


def test_session_policy_is_database_free() -> None:
    idle, absolute = session_expiry(1_000, idle_days=1, absolute_days=2)
    session = AuthSession(
        id="s1",
        user_id=1,
        session_digest=digest_secret("session"),
        csrf_digest=digest_secret("csrf"),
        created_at=1_000,
        last_seen_at=1_000,
        idle_expires_at=idle,
        absolute_expires_at=absolute,
    )
    assert is_session_valid(session, 1_001)
    assert verify_csrf(session, "csrf")
    assert verify_invite_digest(digest_secret("123456"), "123456")
    assert touch_session(
        session,
        now_ms=61_001,
        renewal_window_minutes=1,
        idle_days=1,
    )


def test_orm_rows_map_to_domain_entities() -> None:
    user = user_from_orm(TUser(id=7, username="alice", password="hash", mobile="1"))
    assert user == AuthUser(id=7, username="alice", password_hash="hash", mobile="1")

    session = session_from_orm(
        TUserSession(
            id="s1",
            user_id=7,
            session_digest="digest",
            csrf_digest="csrf",
            created_at=1,
            last_seen_at=2,
            idle_expires_at=3,
            absolute_expires_at=4,
        )
    )
    assert session.id == "s1"
    assert session.user_id == 7


@pytest.mark.asyncio
async def test_sqlalchemy_repositories_never_commit() -> None:
    db = AsyncMock()
    db.add = MagicMock()
    user = AuthUser(id=None, username="alice", password_hash="hash")
    await SqlAlchemyUserRepository(db).add(user)

    session = AuthSession(
        id="s1",
        user_id=1,
        session_digest="digest",
        csrf_digest="csrf",
        created_at=1,
        last_seen_at=1,
        idle_expires_at=2,
        absolute_expires_at=3,
    )
    await SqlAlchemySessionRepository(db).add(session)

    assert db.add.call_count == 2
    assert db.flush.await_count == 2
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()
