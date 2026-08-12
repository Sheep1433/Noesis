"""Unit contracts for ``SafeModelRetryMiddleware``."""

from __future__ import annotations

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import HumanMessage

from noesis.middleware.safe_model_retry_middleware import SafeModelRetryMiddleware


class TransientError(Exception):
    """A fake transient provider error."""


def _request() -> ModelRequest:
    return ModelRequest(model=object(), messages=[HumanMessage(content="hi")])  # type: ignore[arg-type]


def test_retries_transient_error_until_success() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientError("flaky")
        return "ok"

    mw = SafeModelRetryMiddleware(max_retries=3, retry_on=(TransientError,), backoff_factor=0)
    assert mw.wrap_model_call(_request(), handler) == "ok"
    assert calls["n"] == 3


def test_does_not_retry_when_retry_on_empty() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        raise TransientError("nope")

    mw = SafeModelRetryMiddleware(max_retries=3, retry_on=(), backoff_factor=0)
    with pytest.raises(TransientError):
        mw.wrap_model_call(_request(), handler)
    assert calls["n"] == 1


def test_context_overflow_never_retried_routes_to_compaction() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        raise ContextOverflowError("too big")

    mw = SafeModelRetryMiddleware(max_retries=4, retry_on=(ContextOverflowError,), backoff_factor=0)
    with pytest.raises(ContextOverflowError):
        mw.wrap_model_call(_request(), handler)
    assert calls["n"] == 1  # not retried, propagated immediately


def test_gives_up_after_max_retries() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        raise TransientError("always")

    mw = SafeModelRetryMiddleware(max_retries=2, retry_on=(TransientError,), backoff_factor=0)
    with pytest.raises(TransientError):
        mw.wrap_model_call(_request(), handler)
    assert calls["n"] == 3  # 1 initial + 2 retries


def test_non_retryable_exception_not_retried() -> None:
    calls = {"n": 0}

    class Fatal(Exception):
        pass

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        raise Fatal("hard fail")

    mw = SafeModelRetryMiddleware(max_retries=3, retry_on=(TransientError,), backoff_factor=0)
    with pytest.raises(Fatal):
        mw.wrap_model_call(_request(), handler)
    assert calls["n"] == 1


def test_successful_first_call_no_retry() -> None:
    calls = {"n": 0}

    def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        return "ok"

    mw = SafeModelRetryMiddleware(max_retries=3, retry_on=(TransientError,), backoff_factor=0)
    assert mw.wrap_model_call(_request(), handler) == "ok"
    assert calls["n"] == 1


def test_negative_max_retries_rejected() -> None:
    with pytest.raises(ValueError):
        SafeModelRetryMiddleware(max_retries=-1)


@pytest.mark.anyio
async def test_async_retries_transient() -> None:
    calls = {"n": 0}

    async def handler(_req):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] < 2:
            raise TransientError("flaky")
        return "ok"

    mw = SafeModelRetryMiddleware(max_retries=2, retry_on=(TransientError,), backoff_factor=0)
    result = await mw.awrap_model_call(_request(), handler)
    assert result == "ok"
    assert calls["n"] == 2
