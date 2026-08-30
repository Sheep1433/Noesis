"""基础设施异常的用户可见输出契约：原始错误文本不透给终端用户。

事故背景：PG 崩溃恢复期登录报错，`str(exc)` 把
"the database system is not yet accepting connections..." 整句透给了
前端 toast（含基础设施细节）。三类收口：
- OperationalError/InterfaceError（DB 不可达/恢复中）→ 503 + 通用文案
- 其他 SQLAlchemyError（SQL/完整性）→ 500 + 通用文案（无 SQL 细节）
- 未知 Exception → 500 + 通用文案（无路径/连接串等细节）
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError, OperationalError


@pytest.fixture()
def app_with_handlers():
    from server.exception_handlers import handle_exception

    app = FastAPI()
    handle_exception(app)
    return app


def _parse_body(response) -> dict:
    import json

    return json.loads(response.body)


class TestInfraErrorHiding:
    @pytest.mark.asyncio
    async def test_operational_error_maps_503_friendly(self, app_with_handlers) -> None:
        """DB 恢复中/连接失败 → 503 + 通用文案，原始 PG 文本不出现在响应。"""
        from sqlalchemy.exc import SQLAlchemyError

        exc = OperationalError(
            "SELECT", {}, Exception(
                "(psycopg2.OperationalError) the database system is not yet "
                "accepting connections DETAIL: Consistent recovery state has "
                "not been yet reached."
            ),
        )
        handler = app_with_handlers.exception_handlers[SQLAlchemyError]
        response = await handler(None, exc)

        assert response.status_code == 503
        body = _parse_body(response)
        assert body["code"] == 503
        assert body["msg"] == "服务暂时不可用，请稍后重试"
        assert "database system" not in response.body.decode()
        assert "recovery" not in response.body.decode()

    @pytest.mark.asyncio
    async def test_other_sqlalchemy_error_maps_500_generic(self, app_with_handlers) -> None:
        """SQL/完整性异常 → 500 通用文案，SQL 语句与约束名不外泄。"""
        from sqlalchemy.exc import SQLAlchemyError

        exc = IntegrityError(
            "INSERT INTO t_user_llm_provider ...", {},
            Exception("duplicate key value violates unique constraint \"uq_provider_slug\""),
        )
        handler = app_with_handlers.exception_handlers[SQLAlchemyError]
        response = await handler(None, exc)

        assert response.status_code == 500
        body = _parse_body(response)
        assert body["msg"] == "服务器内部错误，请稍后重试"
        assert "INSERT INTO" not in response.body.decode()
        assert "uq_provider_slug" not in response.body.decode()

    @pytest.mark.asyncio
    async def test_generic_exception_maps_500_generic(self, app_with_handlers) -> None:
        """未知异常 → 500 通用文案，str(exc) 细节（路径/连接串）不外泄。"""
        exc = RuntimeError("connection to api.kilo.ai:443 failed: /etc/ssl/cert.pem missing")
        handler = app_with_handlers.exception_handlers[Exception]
        response = await handler(None, exc)

        assert response.status_code == 500
        body = _parse_body(response)
        assert body["msg"] == "服务器内部错误，请稍后重试"
        assert "kilo.ai" not in response.body.decode()
        assert "/etc/ssl" not in response.body.decode()
