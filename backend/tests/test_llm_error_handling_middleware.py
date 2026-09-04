"""LLMErrorHandlingMiddleware retry/fallback behavior.

Locks the contract that transient errors retry (with backoff + retry event) and
that exhausted retries / non-retriable errors emit a fallback event and return a
no-tool-calls AIMessage — the path that downstream translates to RunStatus.ERROR.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from noesis.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware


class _Transient(Exception):
    pass


def _make_request() -> Any:
    # A minimal stand-in; the middleware only reads .messages/.tools/.system_message
    # via _request_tokens when no request_token_counter is configured. We inject a
    # counter to avoid touching the real tokenizer.
    class _Req:
        messages: list = []
        tools: list = []
        system_message: Any = None
        state: dict = {}
        runtime: Any = None

    return _Req()


def _middleware() -> LLMErrorHandlingMiddleware:
    return LLMErrorHandlingMiddleware(max_retries=3, circuit_failure_threshold=99)


@pytest.mark.asyncio
async def test_transient_retries_then_succeeds() -> None:
    calls = {"n": 0}
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def handler(_request: Any) -> Any:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Transient("boom")
        return "ok"

    mw = _middleware()
    mw._classify_error = lambda exc: (True, "transient")  # type: ignore[assignment]

    async def _capture(name: str, payload: dict[str, Any]) -> None:
        emitted.append((name, payload))

    with patch(
        "noesis.agents.middlewares.llm_error_handling_middleware.aemit_noesis_event",
        side_effect=_capture,
    ), patch(
        "noesis.agents.middlewares.llm_error_handling_middleware.asyncio.sleep",
        new=lambda _s: _noop(),
    ):
        result = await mw.awrap_model_call(_make_request(), handler)

    assert result == "ok"
    assert calls["n"] == 3
    # two retry events, no fallback
    assert [name for name, _ in emitted] == ["noesis_model_retry", "noesis_model_retry"]


@pytest.mark.asyncio
async def test_exhausted_retries_emit_fallback_and_return_aimessage() -> None:
    calls = {"n": 0}
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def handler(_request: Any) -> Any:
        calls["n"] += 1
        raise _Transient("persistent boom")

    mw = _middleware()
    mw._classify_error = lambda exc: (True, "transient")  # type: ignore[assignment]

    async def _capture(name: str, payload: dict[str, Any]) -> None:
        emitted.append((name, payload))

    with patch(
        "noesis.agents.middlewares.llm_error_handling_middleware.aemit_noesis_event",
        side_effect=_capture,
    ), patch(
        "noesis.agents.middlewares.llm_error_handling_middleware.asyncio.sleep",
        new=lambda _s: _noop(),
    ):
        result = await mw.awrap_model_call(_make_request(), handler)

    # retried max_retries times total (3 attempts), then fallback
    assert calls["n"] == 3
    names = [name for name, _ in emitted]
    assert names[:-1] == ["noesis_model_retry", "noesis_model_retry"]
    assert names[-1] == "noesis_model_fallback"
    # fallback returns a no-tool-calls AIMessage whose content is the user-facing msg
    assert not getattr(result, "tool_calls", None)


async def _noop() -> None:
    return None


class _Response:
    def __init__(self, headers: dict[str, str], status_code: int = 429) -> None:
        self.headers = headers
        self.status_code = status_code


class _RetryAfterError(Exception):
    def __init__(self, headers: dict[str, str]) -> None:
        super().__init__("Error code: 429 - upstream rate limited")
        self.response = _Response(headers)


def test_retry_after_honored_within_cap() -> None:
    """网关建议 60s（分钟窗限流重置）：遵循，等一个窗口后重试成功率高。"""
    mw = _middleware()
    delay = mw._build_retry_delay_ms(None, _RetryAfterError({"Retry-After": "60"}))
    assert delay == 60_000


def test_retry_after_clamped_to_cap() -> None:
    """病态大值（网关配错 Retry-After: 3600）被 clamp 到上限，不挂死 run。"""
    mw = _middleware()
    delay = mw._build_retry_delay_ms(None, _RetryAfterError({"Retry-After": "3600"}))
    assert delay == mw.retry_after_cap_ms
    # 自定义上限生效
    mw.retry_after_cap_ms = 30_000
    assert mw._build_retry_delay_ms(None, _RetryAfterError({"retry-after-ms": "120000"})) == 30_000


def test_retry_after_absent_falls_back_to_backoff() -> None:
    """无 Retry-After：走自身指数退避（cap 8s），不受 clamp 影响。"""
    mw = _middleware()
    delay = mw._build_retry_delay_ms(None, _RetryAfterError({}))
    assert 0 < delay <= mw.retry_cap_delay_ms


# ---------------------------------------------------------------------------
# 流级空闲超时（StreamIdleTimeoutError）：网关挂流（只发 SSE ping 不出内容）
# ---------------------------------------------------------------------------

def test_stream_idle_timeout_classified_transient() -> None:
    """挂流空闲超时按瞬时错误重试——它是唯一能发现死流的层。"""
    from noesis.llm.factory import StreamIdleTimeoutError

    mw = _middleware()
    retriable, reason = mw._classify_error(
        StreamIdleTimeoutError("No model chunk received in 120s (stream idle)")
    )
    assert retriable is True
    assert reason == "transient"


def test_stream_idle_timeout_fallback_message() -> None:
    """重试耗尽后的失败提示走「模型响应中断」文案（流挂死语义）。"""
    from noesis.llm.factory import StreamIdleTimeoutError

    mw = _middleware()
    msg = mw._fallback_message_for_reason(
        StreamIdleTimeoutError("idle"), "transient"
    )
    assert "模型响应中断" in msg


@pytest.mark.asyncio
async def test_stream_idle_timeout_retries_then_falls_back() -> None:
    """挂流：空闲超时 → 重试 → 耗尽 → 降级 AIMessage（run 可终态）。"""
    from noesis.llm.factory import StreamIdleTimeoutError

    mw = _middleware()
    mw.retry_base_delay_ms = 1
    mw.retry_cap_delay_ms = 1
    calls = {"n": 0}

    async def handler(request: Any) -> Any:
        calls["n"] += 1
        raise StreamIdleTimeoutError("No model chunk received in 120s (stream idle)")

    from unittest.mock import AsyncMock

    retry_mock = AsyncMock()
    fallback_mock = AsyncMock()

    with patch.object(mw, "_aemit_retry", retry_mock), \
         patch.object(mw, "_aemit_fallback", fallback_mock):
        result = await mw.awrap_model_call(_make_request(), handler)

    assert calls["n"] == mw.retry_max_attempts  # 每次尝试都撞空闲超时
    assert retry_mock.await_count == mw.retry_max_attempts - 1
    assert fallback_mock.await_count == 1
    assert "模型响应中断" in result.content
    assert getattr(result, "tool_calls", None) in (None, [])
