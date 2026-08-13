"""Unit contracts for ``SafeModelRetryMiddleware``."""

from __future__ import annotations

import asyncio

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import HumanMessage

from noesis.middleware.safe_model_retry_middleware import (
    SafeModelRetryMiddleware,
    is_transient_model_error,
)


class _FakeAPIStatusError(Exception):
    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(message or f"HTTP {status_code}")


class _FakeRateLimitError(Exception):
    status_code = 429


class _FakeTimeoutError(TimeoutError):
    pass


class _FakeConnectionError(ConnectionError):
    pass


class _FatalError(Exception):
    status_code = 400


def _request() -> ModelRequest:
    return ModelRequest(model=object(), messages=[HumanMessage(content="hi")])  # type: ignore[arg-type]


def test_retries_429_until_success() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeRateLimitError()
        return "ok"

    mw = SafeModelRetryMiddleware(max_retries=3, backoff_factor=0)
    assert mw.wrap_model_call(_request(), handler) == "ok"
    assert calls["n"] == 3


def test_retries_503_server_error() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] < 2:
            raise _FakeAPIStatusError(503)
        return "ok"

    mw = SafeModelRetryMiddleware(max_retries=2, backoff_factor=0)
    assert mw.wrap_model_call(_request(), handler) == "ok"
    assert calls["n"] == 2


def test_retries_500_server_error() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] < 2:
            raise _FakeAPIStatusError(500)
        return "ok"

    mw = SafeModelRetryMiddleware(max_retries=2, backoff_factor=0)
    assert mw.wrap_model_call(_request(), handler) == "ok"


def test_retries_timeout_error() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] < 2:
            raise _FakeTimeoutError("timed out")
        return "ok"

    mw = SafeModelRetryMiddleware(max_retries=2, backoff_factor=0)
    assert mw.wrap_model_call(_request(), handler) == "ok"


def test_retries_connection_error() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] < 2:
            raise _FakeConnectionError("dropped")
        return "ok"

    mw = SafeModelRetryMiddleware(max_retries=2, backoff_factor=0)
    assert mw.wrap_model_call(_request(), handler) == "ok"


def test_context_overflow_never_retried_routes_to_compaction() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        raise ContextOverflowError("too big")

    mw = SafeModelRetryMiddleware(max_retries=4, backoff_factor=0)
    with pytest.raises(ContextOverflowError):
        mw.wrap_model_call(_request(), handler)
    assert calls["n"] == 1


def test_gives_up_after_max_retries() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        raise _FakeRateLimitError()

    mw = SafeModelRetryMiddleware(max_retries=2, backoff_factor=0)
    with pytest.raises(_FakeRateLimitError):
        mw.wrap_model_call(_request(), handler)
    assert calls["n"] == 3  # 1 initial + 2 retries


def test_non_retryable_400_not_retried() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        raise _FatalError("bad request")

    mw = SafeModelRetryMiddleware(max_retries=3, backoff_factor=0)
    with pytest.raises(_FatalError):
        mw.wrap_model_call(_request(), handler)
    assert calls["n"] == 1


def test_successful_first_call_no_retry() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        return "ok"

    mw = SafeModelRetryMiddleware(max_retries=3, backoff_factor=0)
    assert mw.wrap_model_call(_request(), handler) == "ok"
    assert calls["n"] == 1


def test_negative_max_retries_rejected() -> None:
    with pytest.raises(ValueError):
        SafeModelRetryMiddleware(max_retries=-1)


@pytest.mark.anyio
async def test_async_retries_429() -> None:
    calls = {"n": 0}

    async def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] < 2:
            raise _FakeRateLimitError()
        return "ok"

    mw = SafeModelRetryMiddleware(max_retries=2, backoff_factor=0)
    result = await mw.awrap_model_call(_request(), handler)
    assert result == "ok"
    assert calls["n"] == 2


def test_is_transient_model_error_classification() -> None:
    assert is_transient_model_error(_FakeAPIStatusError(429)) is True
    assert is_transient_model_error(_FakeAPIStatusError(500)) is True
    assert is_transient_model_error(_FakeAPIStatusError(503)) is True
    assert is_transient_model_error(_FakeAPIStatusError(408)) is True
    assert is_transient_model_error(_FatalError()) is False  # 400
    assert is_transient_model_error(_FakeTimeoutError()) is True
    assert is_transient_model_error(_FakeConnectionError()) is True
    assert is_transient_model_error(ValueError("not transient")) is False
