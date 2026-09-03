"""LLM error handling middleware with retry/backoff and user-facing fallbacks.

在 wrap_model_call 层捕获 LLM provider 异常，重试瞬时错误（连接失败/超时/5xx/429），
重试时发 ``noesis_model_retry`` custom event（bridge 转为 run-status SSE）；重试
耗尽或不可重试时发 ``noesis_model_fallback`` custom event，bridge 接收后把失败
说明文本推入 SSE 流（text-delta）并标 error 终态，用户在消息体看到失败原因。

circuit breaker：连续失败达阈值后熔断，后续请求直接降级不再调用 provider。
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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

from noesis.agents.middlewares._events import aemit_noesis_event, emit_noesis_event
from noesis.config.env import ModelConfig
from noesis.llm.finish_reason import normalize_provider_finish_reason
from noesis.runtime.logging import logger

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

# 流式响应中断等异常类名，用于重试耗尽后的针对性失败提示
_STREAM_DROP_EXCEPTIONS: frozenset[str] = frozenset(
    {"StreamChunkTimeoutError", "StreamIdleTimeoutError"}
)

# Circuit breaker 默认阈值
_DEFAULT_CIRCUIT_FAILURE_THRESHOLD = 5
_DEFAULT_CIRCUIT_RECOVERY_TIMEOUT_SEC = 60

# 熔断打开时直接降级返回的固定文案
_CIRCUIT_BREAKER_MESSAGE = "服务暂时不可用，请稍候再试。"

# 降级 AIMessage 标记：run 终态判定（executor）据此把 completed 改判 error，
# 避免「失败说明文本」被当成正常产出、父 Agent 误认任务成功。
FALLBACK_MARKER = "noesis_model_fallback"


def _fallback_aimessage(content: str) -> AIMessage:
    return AIMessage(content=content, additional_kwargs={FALLBACK_MARKER: True})


def warn_truncated_tool_calls(response: ModelResponse) -> ModelResponse:
    """finish_reason=length 且带 tool_calls → 参数大概率被流式截断，告警留痕。

    Provider 变体输出上限较低时，长工具参数（如 write_file 的大段
    content）会在 JSON 中途被截断：排在后段的参数（file_path）整个
    丢失，残缺参数进入工具后以「缺字段」形式失败。不改写响应——
    工具错误反馈给模型的恢复路径（缩短输出重试）已验证有效，这里
    只补足可观测性，配合文件工具层的明确报错定位根因。
    """
    messages = response if isinstance(response, list) else [response]
    for message in messages:
        if (
            isinstance(message, AIMessage)
            and message.tool_calls
            and normalize_provider_finish_reason(
                (message.response_metadata or {}).get("finish_reason")
            ) == "length"
        ):
            logger.warning(
                "LLM 输出被截断（finish_reason=length），{} 个工具调用的参数可能不完整：{}",
                len(message.tool_calls),
                [call.get("name") for call in message.tool_calls],
            )
    return response


def is_model_fallback_message(message: object) -> bool:
    """该消息是否为 LLM 重试耗尽后的降级失败说明（而非模型真实产出）。"""
    return bool(getattr(message, "additional_kwargs", {}).get(FALLBACK_MARKER))


@dataclass(frozen=True)
class _FailureOutcome:
    """Classification result of a model-call failure.

    ``should_retry=True`` → caller emits retry event, sleeps ``wait_ms``, loops;
    ``should_retry=False`` → caller emits fallback with ``fallback_message``.
    Only one of ``wait_ms``/``fallback_message`` is meaningful per outcome.
    """

    should_retry: bool
    wait_ms: int = 0
    reason: str = ""
    fallback_message: str = ""


@dataclass(frozen=True)
class _RetryHooks:
    """Sync I/O seams isolated from the retry decision core."""

    sleep: Callable[[int], None]
    emit_retry: Callable[[int, int, str], None]
    emit_fallback: Callable[[str], None]


@dataclass(frozen=True)
class _AsyncRetryHooks:
    """Async I/O seams isolated from the retry decision core."""

    asleep: Callable[[int], Awaitable[None]]
    aemit_retry: Callable[[int, int, str], Awaitable[None]]
    aemit_fallback: Callable[[str], Awaitable[None]]


class LLMErrorHandlingMiddleware(AgentMiddleware[AgentState]):
    """Retry transient LLM errors and surface graceful fallbacks.

    重试与失败轮次感知都在 ``wrap_model_call`` 一处完成：
    - 可重试异常 → 退避后重试，每次发 ``noesis_model_retry`` custom event
    - 重试用尽 / 不可重试 → 发 ``noesis_model_fallback`` custom event（bridge
      把失败文本推入 SSE 流并标 ERROR），返回 content 为失败说明的 AIMessage
    """

    retry_base_delay_ms: int = 1000
    retry_cap_delay_ms: int = 8000
    burst_retry_base_delay_ms: int = 5000
    # Retry-After / retryAfterSeconds 遵循上限：限流窗口语义真实（分钟窗
    # 重置），遵循优于自行指数退避（提前重试只会烧 attempt 预算）；但须
    # clamp 防病态大值（网关配错 Retry-After: 3600 会挂死 run 一小时）
    retry_after_cap_ms: int = 60_000

    def __init__(
        self,
        *,
        max_retries: int | None = None,
        circuit_failure_threshold: int = _DEFAULT_CIRCUIT_FAILURE_THRESHOLD,
        circuit_recovery_timeout_sec: int = _DEFAULT_CIRCUIT_RECOVERY_TIMEOUT_SEC,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        # 显式注入优先；未提供时回退到全局 ModelConfig（保持旧默认行为）。
        resolved = max_retries if max_retries is not None else ModelConfig.max_retries
        self.retry_max_attempts = max(1, int(resolved))
        self.circuit_failure_threshold = circuit_failure_threshold
        self.circuit_recovery_timeout_sec = circuit_recovery_timeout_sec
        # threading.Lock (not asyncio.Lock) because the critical sections in
        # _check_circuit / _record_* perform no await and must also guard the
        # sync wrap_model_call path. Locking is momentary and never spans I/O.
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
                    "Circuit breaker probe failed (Open). Will probe again after {}s.",
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
                        "Circuit breaker tripped (Open). Threshold reached ({}). Will probe after {}s.",
                        self.circuit_failure_threshold,
                        self.circuit_recovery_timeout_sec,
                    )

    def _release_half_open_probe(self) -> None:
        with self._circuit_lock:
            if self._circuit_state == "half_open":
                self._circuit_probe_in_flight = False

    # ---------- error classification ----------

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
            # 网关挂流（只发 SSE ping 不出内容）时的流级空闲超时：
            # 读超时被 ping 续命，这是唯一能发现死流的层，按瞬时错误重试
            "StreamIdleTimeoutError",
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
            return min(retry_after, self.retry_after_cap_ms)
        base = self.burst_retry_base_delay_ms if reason == "burst_rate" else self.retry_base_delay_ms
        cap = self.retry_cap_delay_ms
        seed = base if prev_delay_ms is None else prev_delay_ms
        high = min(cap, max(base, seed * 3))
        if high < base:
            return cap
        return random.randint(base, high)

    # ---------- fallback messages ----------

    def _fallback_message_for_reason(self, exc: BaseException, reason: str) -> str:
        """根据错误分类构造面向用户的失败说明文本。"""
        detail = _extract_error_detail(exc)
        if reason == "quota":
            return "API 额度不足，请检查 provider 账户后重试。"
        if reason == "auth":
            return "API 鉴权失败，请检查凭证后重试。"
        if reason == "burst_rate":
            return "请求过于频繁，请稍候再试。"
        if reason in {"busy", "transient"}:
            if type(exc).__name__ in _STREAM_DROP_EXCEPTIONS:
                return "模型响应中断，请尝试拆分为更小的步骤后重试。"
            return "服务暂时不可用，请稍候重试。"
        return f"LLM 请求失败：{detail}"

    # ---------- custom events ----------

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

    @staticmethod
    def _build_fallback_event(content: str) -> dict[str, Any]:
        return {"type": "noesis_model_fallback", "content": content}

    def _emit_retry(
        self, attempt: int, wait_ms: int, reason: str
    ) -> None:
        payload = self._build_retry_event(
            attempt, wait_ms, reason, max_attempts=self.retry_max_attempts
        )
        emit_noesis_event("noesis_model_retry", payload)

    async def _aemit_retry(
        self, attempt: int, wait_ms: int, reason: str
    ) -> None:
        payload = self._build_retry_event(
            attempt, wait_ms, reason, max_attempts=self.retry_max_attempts
        )
        await aemit_noesis_event("noesis_model_retry", payload)

    def _emit_fallback(self, content: str) -> None:
        emit_noesis_event(
            "noesis_model_fallback", self._build_fallback_event(content)
        )

    async def _aemit_fallback(self, content: str) -> None:
        await aemit_noesis_event(
            "noesis_model_fallback", self._build_fallback_event(content)
        )

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        if self._check_circuit():
            self._emit_fallback(_CIRCUIT_BREAKER_MESSAGE)
            return _fallback_aimessage(_CIRCUIT_BREAKER_MESSAGE)
        return self._run_with_retry(request, handler, self._sync_retry_hooks())

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        if self._check_circuit():
            await self._aemit_fallback(_CIRCUIT_BREAKER_MESSAGE)
            return _fallback_aimessage(_CIRCUIT_BREAKER_MESSAGE)
        return await self._arun_with_retry(request, handler, self._async_retry_hooks())

    # ---------- shared retry core ----------

    def _run_with_retry(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
        hooks: "_RetryHooks",
    ) -> ModelCallResult:
        attempt = 1
        prev_delay_ms: int | None = None
        while True:
            try:
                response = handler(request)
                self._record_success()
                return warn_truncated_tool_calls(response)
            except GraphBubbleUp:
                self._release_half_open_probe()
                raise
            except Exception as exc:
                outcome = self._handle_call_failure(exc, attempt, prev_delay_ms)
                if outcome.should_retry:
                    hooks.emit_retry(attempt, outcome.wait_ms, outcome.reason)
                    hooks.sleep(outcome.wait_ms)
                    prev_delay_ms = outcome.wait_ms
                    attempt += 1
                    continue
                hooks.emit_fallback(outcome.fallback_message)
                return _fallback_aimessage(outcome.fallback_message)

    async def _arun_with_retry(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        hooks: "_AsyncRetryHooks",
    ) -> ModelCallResult:
        attempt = 1
        prev_delay_ms: int | None = None
        while True:
            try:
                response = await handler(request)
                self._record_success()
                return warn_truncated_tool_calls(response)
            except GraphBubbleUp:
                self._release_half_open_probe()
                raise
            except Exception as exc:
                outcome = self._handle_call_failure(exc, attempt, prev_delay_ms)
                if outcome.should_retry:
                    await hooks.aemit_retry(attempt, outcome.wait_ms, outcome.reason)
                    await hooks.asleep(outcome.wait_ms)
                    prev_delay_ms = outcome.wait_ms
                    attempt += 1
                    continue
                await hooks.aemit_fallback(outcome.fallback_message)
                return _fallback_aimessage(outcome.fallback_message)

    def _handle_call_failure(
        self, exc: BaseException, attempt: int, prev_delay_ms: int | None,
    ) -> "_FailureOutcome":
        """Classify, log, and decide retry vs fallback for a model-call exception.

        Side effects restricted to logging and breaker bookkeeping; emitting the
        retry/fallback event and sleeping is left to the caller so sync/async
        hooks stay isolated.
        """
        retriable, reason = self._classify_error(exc)
        if retriable and attempt < self.retry_max_attempts:
            wait_ms = self._build_retry_delay_ms(prev_delay_ms, exc, reason)
            logger.warning(
                "Transient LLM error on attempt {}/{}; retrying in {}ms: {}",
                attempt,
                self.retry_max_attempts,
                wait_ms,
                _extract_error_detail(exc),
            )
            return _FailureOutcome(should_retry=True, wait_ms=wait_ms, reason=reason)
        logger.opt(exception=exc).error(
            "LLM call failed after {} attempt(s): {}",
            attempt,
            _extract_error_detail(exc),
        )
        if retriable and reason != "burst_rate":
            self._record_failure()
        else:
            self._release_half_open_probe()
        msg = self._fallback_message_for_reason(exc, reason)
        return _FailureOutcome(should_retry=False, fallback_message=msg)

    def _sync_retry_hooks(self) -> "_RetryHooks":
        return _RetryHooks(
            sleep=lambda ms: time.sleep(ms / 1000),
            emit_retry=self._emit_retry,
            emit_fallback=self._emit_fallback,
        )

    def _async_retry_hooks(self) -> "_AsyncRetryHooks":
        return _AsyncRetryHooks(
            asleep=lambda ms: asyncio.sleep(ms / 1000),
            aemit_retry=self._aemit_retry,
            aemit_fallback=self._aemit_fallback,
        )


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
