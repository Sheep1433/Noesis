"""认证接口用例（integration）：register / 会话列表 / 撤销指定会话。

按用户裁决不测「退出登录」功能（logout / logout-all 为账号级副作用，
且无业务独立逻辑值得接口级验证）；本文件其余用例不触 LLM。

register 需要有效 6 位邀请码——测试侧直接调 ``RegistrationInviteService.rotate``
轮换取回明文（与本套件文件层播种惯例一致）。撤销单会话用测试内自建的
独立登录态操作，不碰共享的 ``auth_client``。

前置与运行：

    cd backend && uv run app.py
    uv run pytest tests/api/test_auth_admin_api.py -m 'integration and not llm'
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

pytestmark = [pytest.mark.integration]

_BASE_URL = "http://127.0.0.1:8089"
_USERNAME = os.environ.get("NOESIS_TEST_USER", "test")
_PASSWORD = os.environ.get("NOESIS_TEST_PASSWORD", "123456")


def _login(username: str = _USERNAME, password: str = _PASSWORD) -> httpx.Client:
    """独立登录态：自建 client + cookie jar + CSRF 头。"""
    client = httpx.Client(
        base_url=_BASE_URL,
        timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=10.0),
    )
    resp = client.post(
        "/api/auth/login", data={"username": username, "password": password}
    )
    resp.raise_for_status()
    client.headers["X-CSRF-Token"] = resp.json()["data"]["csrf_token"]
    return client


def _rotate_invite_code() -> str:
    """轮换 admin 邀请码并返回明文（走服务层，与 API 同一套 digest 校验）。

    每次调用新建独立 engine：全局 pg_manager 的连接池绑定创建它的
    事件循环，跨 ``asyncio.run`` 复用会报跨 loop 错误。
    """
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from noesis.services.auth.invites import RegistrationInviteService
    from noesis.storage.postgres.manager import ASYNC_SQLALCHEMY_DATABASE_URL

    async def run() -> str:
        engine = create_async_engine(ASYNC_SQLALCHEMY_DATABASE_URL)
        try:
            session_maker = async_sessionmaker(engine, expire_on_commit=False)
            async with session_maker() as session:
                return await RegistrationInviteService.rotate(session, "admin")
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _delete_user_by_username(username: str) -> None:
    """删除一次性注册用户及其会话/数据目录（register 无删除 API，走 DB）。"""
    import asyncio
    import shutil

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from noesis.config.user_data_paths import get_user_root
    from noesis.storage.postgres.manager import ASYNC_SQLALCHEMY_DATABASE_URL

    async def run() -> None:
        engine = create_async_engine(ASYNC_SQLALCHEMY_DATABASE_URL)
        try:
            maker = async_sessionmaker(engine)
            async with maker() as db:
                uid = (
                    await db.execute(
                        text("SELECT id FROM t_user WHERE username = :u"),
                        {"u": username},
                    )
                ).scalar()
                if uid is None:
                    return
                await db.execute(
                    text("DELETE FROM t_user_session WHERE user_id = :u"), {"u": uid}
                )
                await db.execute(
                    text("DELETE FROM t_user WHERE id = :u"), {"u": uid}
                )
                await db.commit()
                return uid
        finally:
            await engine.dispose()

    uid = asyncio.run(run())
    if uid:
        shutil.rmtree(get_user_root(uid), ignore_errors=True)


def test_register_with_valid_invite_creates_user_and_session() -> None:
    """有效邀请码注册：创建用户 + 直接返回登录态（csrf_token + cookie）。"""
    invite_code = _rotate_invite_code()
    username = f"api-reg-{uuid.uuid4().hex[:8]}"

    client = httpx.Client(base_url=_BASE_URL, timeout=30.0)
    try:
        resp = client.post(
            "/api/auth/register",
            json={
                "username": username,
                "password": "reg-test-123456",
                "invite_code": invite_code,
            },
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        assert data.get("csrf_token"), "注册应直接下发登录态"
        assert "noesis_session" in resp.cookies or "noesis_session" in client.cookies

        # 新用户会话可用：GET /auth/session 正常轮换 CSRF
        client.headers["X-CSRF-Token"] = data["csrf_token"]
        resp = client.get("/api/auth/session")
        resp.raise_for_status()
        assert resp.json()["data"]["user"]["username"] == username
    finally:
        client.close()
        _delete_user_by_username(username)


def test_session_list_and_revoke_single_session() -> None:
    """会话列表标记 current；撤销指定会话后该会话立即失效（半径仅测试自建会话）。"""
    main = _login()
    second = _login()
    try:
        # 先取 second 自己的 session id（避免误伤同用户其它登录态）
        resp = second.get("/api/auth/session")
        resp.raise_for_status()
        second_session_id = resp.json()["data"]["session"]["id"]

        resp = main.get("/api/auth/sessions")
        resp.raise_for_status()
        sessions = resp.json()["data"]["sessions"]
        assert sessions, "至少应有当前登录会话"
        assert len([s for s in sessions if s["current"]]) == 1, "current 标记应唯一"
        assert any(s["id"] == second_session_id for s in sessions)

        resp = main.delete(f"/api/auth/sessions/{second_session_id}")
        resp.raise_for_status()

        resp = second.get("/api/auth/session")
        assert resp.status_code == 401, "被撤销会话应立即 401"
        resp = main.get("/api/auth/session")
        resp.raise_for_status()
    finally:
        main.close()
        second.close()


def test_query_user_record_returns_paginated_records(auth_client) -> None:
    """遗留 query_user_record：分页 records + total_count 结构。"""
    resp = auth_client.post(
        "/api/user/query_user_record",
        json={"search_text": "", "page": 1, "limit": 10},
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    assert "records" in data and "total_count" in data and "total_pages" in data
    assert isinstance(data["records"], list)
