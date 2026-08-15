"""LLM error handling middleware with retry/backoff and user-facing fallbacks.

迁移自 deer-flow 的 LLMErrorHandlingMiddleware。在 wrap_model_call 层捕获
LLM provider 异常，重试瞬时错误（连接失败/超时/5xx/429），重试时发
``noesis_model_retry`` custom event（bridge 已实现接收转 run-status SSE）；
重试用尽后返回一条 content 为失败说明的 AIMessage，让下一轮模型感知上一轮
被中断，而不是把半截回答当真实内容。
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import Any
from typing_extensions import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from noesis.config.env import ModelConfig

logger = logging.getLogger(__name__)

# 可重试的 HTTP 状态码（与 OpenAI SDK _should_retry 一致）
_RETRIABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
_BUSY_PATTERNS = (
    "server busy",
    "temporarily unavailable",
    "try again later",
    "please retry",
    "please try again",
    "overloaded",
    "high demand",
    "rate limit",
    "负载较高",
    "服务繁忙",
    "稍后重试",
    "请稍后重试",
)
_QUOTA_PATTERNS = (
    "insufficient_quota",
    "quota",
    "billing",
    "credit",
    "payment",
    "余额不足",
    "超出限额",
    "额度不足",
    "欠费",
)
_AUTH_PATTERNS = (
    "authentication",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "permission",
    "forbidden",
    "access denied",
    "无权",
    "未授权",
)
_BURST_PATTERNS = (
    "limit_burst_rate",
    "rate increased too quickly",
    "burst rate",
    "请求速率增长过快",
    "突发速率",
)

# Stream-chunk 超时等异常类名，仅 1 次重试后快速失败
_RETRY_BUDGET_OVERRIDES: dict[str, int] = {
    "StreamChunkTimeoutError": 2,
}
_REASON_RETRY_BUDGETS: dict[str, int] = {
    "burst_rate": 2,
}
_STREAM_DROP_EXCEPTIONS: frozenset[str] = frozenset({"StreamChunkTimeoutError"})

# Circuit breaker 默认阈值
_DEFAULT_CIRCUIT_FAILURE_THRESHOLD = 5
_DEFAULT_CIRCUIT_RECOVERY_TIMEOUT_SEC = 60


class LLMErrorHandlingMiddleware(AgentMiddleware[AgentState]):
    """Retry transient LLM errors and surface graceful assistant messages.

    重试与失败轮次感知都在 ``wrap_model_call`` 一处完成：
    - 可重试异常 → 退避后重试，每次发 ``noesis_model_retry`` custom event
    - 重试用尽 / 不可重试 → 返回 content 为失败说明的 AIMessage（带
      ``noesis_error_fallback=True`` 标记），让下一轮模型感知上一轮中断
    """

    retry_base_delay_ms: int = 1000
    retry_cap_delay_ms: int = 8000
    burst_retry_base_delay_ms: int = 5000

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.retry_max_attempts = max(1, int(ModelConfig.max_retries))
        self.circuit_failure_threshold = _DEFAULT_CIRCUIT_FAILURE_THRESHOLD
        self.circuit_recovery_timeout_sec = _DEFAULT_CIRCUIT_RECOVERY_TIMEOUT_SEC
        self._circuit_lock = threading.Lock()
        self._circuit_failure_count = 0
        self._circuit_open_until = 0.0
        self._circuit_state = "closed"
        self._circuit_probe_in_flight = False

    # ---------- circuit breaker ----------

    def _check_circuit(self) -> bool:
        with self._circuit_lock:
            now = time.time()
            if self._circuit_state == "open":
                if now < self._circuit_open_until:
                    return True
                self._circuit_state = "half_open"
                self._circuit_probe_in_flight = False
            if self._circuit_state == "half_open":
                if self._circuit_probe_in_flight:
                    return True
                self._circuit_probe_in_flight = True
                return False
            return False

    def _record_success(self) -> None:
        with self._circuit_lock:
            if self._circuit_state != "closed" or self._circuit_failure_count > 0:
                logger.info("Circuit breaker reset (Closed). LLM service recovered.")
            self._circuit_failure_count = 0
            self._circuit_open_until = 0.0
            self._circuit_state = "closed"
            self._circuit_probe_in_flight = False

    def _record_failure(self) -> None:
        with self._circuit_lock:
            if self._circuit_state == "half_open":
                self._circuit_open_until = time.time() + self.circuit_recovery_timeout_sec
                self._circuit_state = "open"
                self._circuit_probe_in_flight = False
                logger.error(
                    "Circuit breaker probe failed (Open). Will probe again after %ds.",
                    self.circuit_recovery_timeout_sec,
                )
                return
            self._circuit_failure_count += 1
            if self._circuit_failure_count >= self.circuit_failure_threshold:
                self._circuit_open_until = time.time() + self.circuit_recovery_timeout_sec
                if self._circuit_state != "open":
                    self._circuit_state = "open"
                    self._circuit_probe_in_flight = False
                    logger.error(
                        "Circuit breaker tripped (Open). Threshold reached (%d). Will probe after %ds.",
                        self.circuit_failure_threshold,
                        self.circuit_recovery_timeout_sec,
                    )

    def _release_half_open_probe(self) -> None:
        with self._circuit_lock:
            if self._circuit_state == "half_open":
                self._circuit_probe_in_flight = False

    # ---------- error classification ----------

    def _max_attempts_for(self, exc: BaseException, reason: str = "transient") -> int:
        candidates = [self.retry_max_attempts]
        class_override = _RETRY_BUDGET_OVERRIDES.get(type(exc).__name__)
        if class_override is not None:
            candidates.append(class_override)
        reason_override = _REASON_RETRY_BUDGETS.get(reason)
        if reason_override is not None:
            candidates.append(reason_override)
        return min(candidates)

    def _classify_error(self, exc: BaseException) -> tuple[bool, str]:
        detail = _extract_error_detail(exc)
        lowered = detail.lower()
        error_code = _extract_error_code(exc)
        status_code = _extract_status_code(exc)

        if _matches_any(lowered, _QUOTA_PATTERNS) or _matches_any(str(error_code).lower(), _QUOTA_PATTERNS):
            return False, "quota"
        if _matches_any(lowered, _AUTH_PATTERNS):
            return False, "auth"
        if _matches_any(lowered, _BURST_PATTERNS) or _matches_any(str(error_code).lower(), _BURST_PATTERNS):
            return True, "burst_rate"

        exc_name = exc.__class__.__name__
        if exc_name in {
            "APITimeoutError",
            "APIConnectionError",
            "InternalServerError",
            "ReadError",
            "RemoteProtocolError",
            "StreamChunkTimeoutError",
        }:
            return True, "transient"
        if isinstance(exc, IndexError):
            return True, "transient"
        if status_code in _RETRIABLE_STATUS_CODES:
            return True, "transient"
        if _matches_any(lowered, _BUSY_PATTERNS):
            return True, "busy"
        return False, "generic"

    # ---------- backoff ----------

    def _build_retry_delay_ms(self, prev_delay_ms: int | None, exc: BaseException, reason: str = "transient") -> int:
        retry_after = _extract_retry_after_ms(exc)
        if retry_after is not None:
            return retry_after
        base = self.burst_retry_base_delay_ms if reason == "burst_rate" else self.retry_base_delay_ms
        cap = self.retry_cap_delay_ms
        seed = base if prev_delay_ms is None else prev_delay_ms
        high = min(cap, max(base, seed * 3))
        if high < base:
            return cap
        return random.randint(base, high)

    # ---------- fallback messages ----------

    def _build_circuit_breaker_message(self) -> str:
        return "LLM 服务因连续失败已触发熔断保护，请稍候再试。"

    def _build_user_message(self, exc: BaseException, reason: str) -> str:
        detail = _extract_error_detail(exc)
        if reason == "quota":
            return "LLM 服务额度不足或计费不可用，请检查 provider 账户后重试。"
        if reason == "auth":
            return "LLM 服务鉴权失败，请检查 provider 凭证后重试。"
        if reason == "burst_rate":
            return "LLM 服务因请求速率增长过快被临时限流，请稍候再试。"
        if reason in {"busy", "transient"}:
            if type(exc).__name__ in _STREAM_DROP_EXCEPTIONS:
                return (
                    "模型流式响应在中途中断，通常因单次响应或工具调用过大导致。"
                    "请尝试拆分为更小的步骤后重试。"
                )
            return "LLM 服务经多次重试后仍不可用，请稍候继续对话。"
        return f"LLM 请求失败：{detail}"

    def _build_error_fallback_message(
        self,
        content: str,
        *,
        error_type: str,
        reason: str,
        detail: str,
    ) -> AIMessage:
        return AIMessage(
            content=content,
            additional_kwargs={
                "noesis_error_fallback": True,
                "error_type": error_type,
                "error_reason": reason,
                "error_detail": detail,
            },
        )

    def _build_user_fallback_message(self, exc: BaseException, reason: str) -> AIMessage:
        return self._build_error_fallback_message(
            self._build_user_message(exc, reason),
            error_type=type(exc).__name__,
            reason=reason,
            detail=_extract_error_detail(exc),
        )

    # ---------- retry events ----------

    def _build_retry_event(
        self,
        attempt: int,
        wait_ms: int,
        reason: str,
        *,
        max_attempts: int,
    ) -> dict[str, Any]:
        seconds = max(1, round(wait_ms / 1000))
        reason_text = {
            "busy": "provider 繁忙",
            "burst_rate": "provider 限流请求速率",
        }.get(reason, "provider 请求暂时失败")
        return {
            "type": "noesis_model_retry",
            "status": "retrying",
            "attempt_id": attempt,
            "max_attempts": max_attempts,
            "wait_ms": wait_ms,
            "reason": reason,
            "message": f"连接失败，{reason_text}，正在重试 ({attempt}/{max_attempts})，{seconds}s 后重试",
        }

    def _emit_retry_event(
        self,
        attempt: int,
        wait_ms: int,
        reason: str,
        *,
        max_attempts: int,
    ) -> None:
        try:
            from langgraph.config import get_stream_writer

            payload = self._build_retry_event(attempt, wait_ms, reason, max_attempts=max_attempts)
            writer = get_stream_writer()
            try:
                writer(payload)
            except Exception:
                pass
            from langchain_core.callbacks import dispatch_custom_event

            dispatch_custom_event("noesis_model_retry", payload)
        except GraphBubbleUp:
            raise
        except Exception:
            logger.debug("Failed to emit noesis_model_retry event", exc_info=True)

    async def _aemit_retry_event(
        self,
        attempt: int,
        wait_ms: int,
        reason: str,
        *,
        max_attempts: int,
    ) -> None:
        try:
            from langgraph.config import get_stream_writer

            payload = self._build_retry_event(attempt, wait_ms, reason, max_attempts=max_attempts)
            writer = get_stream_writer()
            try:
                writer(payload)
            except Exception:
                pass
            from langchain_core.callbacks import adispatch_custom_event

            await adispatch_custom_event("noesis_model_retry", payload)
        except GraphBubbleUp:
            raise
        except Exception:
            logger.debug("Failed to emit async noesis_model_retry event", exc_info=True)

    # ---------- model call wrapping ----------

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        if self._check_circuit():
            return self._build_error_fallback_message(
                self._build_circuit_breaker_message(),
                error_type="CircuitBreakerOpen",
                reason="circuit_open",
                detail="LLM circuit breaker is open",
            )

        attempt = 1
        prev_delay_ms: int | None = None
        while True:
            try:
                response = handler(request)
                self._record_success()
                return response
            except GraphBubbleUp:
                self._release_half_open_probe()
                raise
            except Exception as exc:
                retriable, reason = self._classify_error(exc)
                max_attempts = self._max_attempts_for(exc, reason)
                if retriable and attempt < max_attempts:
                    wait_ms = self._build_retry_delay_ms(prev_delay_ms, exc, reason)
                    prev_delay_ms = wait_ms
                    logger.warning(
                        "Transient LLM error on attempt %d/%d; retrying in %dms: %s",
                        attempt,
                        max_attempts,
                        wait_ms,
                        _extract_error_detail(exc),
                    )
                    self._emit_retry_event(attempt, wait_ms, reason, max_attempts=max_attempts)
                    time.sleep(wait_ms / 1000)
                    attempt += 1
                    continue
                logger.warning(
                    "LLM call failed after %d attempt(s): %s",
                    attempt,
                    _extract_error_detail(exc),
                    exc_info=exc,
                )
                if retriable and reason != "burst_rate":
                    self._record_failure()
                else:
                    self._release_half_open_probe()
                return self._build_user_fallback_message(exc, reason)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        if self._check_circuit():
            return self._build_error_fallback_message(
                self._build_circuit_breaker_message(),
                error_type="CircuitBreakerOpen",
                reason="circuit_open",
                detail="LLM circuit breaker is open",
            )

        attempt = 1
        prev_delay_ms: int | None = None
        while True:
            try:
                response = await handler(request)
                self._record_success()
                return response
            except GraphBubbleUp:
                self._release_half_open_probe()
                raise
            except Exception as exc:
                retriable, reason = self._classify_error(exc)
                max_attempts = self._max_attempts_for(exc, reason)
                if retriable and attempt < max_attempts:
                    wait_ms = self._build_retry_delay_ms(prev_delay_ms, exc, reason)
                    prev_delay_ms = wait_ms
                    logger.warning(
                        "Transient LLM error on attempt %d/%d; retrying in %dms: %s",
                        attempt,
                        max_attempts,
                        wait_ms,
                        _extract_error_detail(exc),
                    )
                    await self._aemit_retry_event(attempt, wait_ms, reason, max_attempts=max_attempts)
                    await asyncio.sleep(wait_ms / 1000)
                    attempt += 1
                    continue
                logger.warning(
                    "LLM call failed after %d attempt(s): %s",
                    attempt,
                    _extract_error_detail(exc),
                    exc_info=exc,
                )
                if retriable and reason != "burst_rate":
                    self._record_failure()
                else:
                    self._release_half_open_probe()
                return self._build_user_fallback_message(exc, reason)


def _matches_any(detail: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in detail for pattern in patterns)


def _extract_error_code(exc: BaseException) -> Any:
    for attr in ("code", "error_code"):
        value = getattr(exc, attr, None)
        if value not in (None, ""):
            return value
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            for key in ("code", "type"):
                value = error.get(key)
                if value not in (None, ""):
                    return value
    return None


def _extract_status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _extract_retry_after_ms(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = None
    header_name = ""
    for key in ("retry-after-ms", "Retry-After-Ms", "retry-after", "Retry-After"):
        header_name = key
        if hasattr(headers, "get"):
            raw = headers.get(key)
        if raw:
            break
    if not raw:
        return None
    try:
        multiplier = 1 if "ms" in header_name.lower() else 1000
        return max(0, int(float(raw) * multiplier))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(raw))
            delta = target.timestamp() - time.time()
            return max(0, int(delta * 1000))
        except (TypeError, ValueError, OverflowError):
            return None


def _extract_error_detail(exc: BaseException) -> str:
    detail = str(exc).strip()
    if detail:
        return detail
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message.strip():
        return message.strip()
    return exc.__class__.__name__


__all__ = ["LLMErrorHandlingMiddleware"]
