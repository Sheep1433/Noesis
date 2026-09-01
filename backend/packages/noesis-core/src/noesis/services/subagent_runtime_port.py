"""子 Agent 执行器与会话用例之间的窄端口。

运行器和产品服务都依赖这里的协议，不相互 import。应用启动时由各自模块注册
实现，避免 executor ↔ service 的循环依赖，同时保持运行时调用是显式的。
"""

from __future__ import annotations

from typing import Any

_SERVICE: Any = None
_EXECUTOR: Any = None


def configure_service_port(service: Any) -> None:
    global _SERVICE
    _SERVICE = service


def configure_executor_port(executor: Any) -> None:
    global _EXECUTOR
    _EXECUTOR = executor


def _service() -> Any:
    if _SERVICE is None:
        raise RuntimeError("subagent session port is not configured")
    return _SERVICE


def _executor() -> Any:
    if _EXECUTOR is None:
        raise RuntimeError("subagent executor port is not configured")
    return _EXECUTOR


class SubagentSessionPort:
    @staticmethod
    async def mark_started(*args: Any, **kwargs: Any) -> Any:
        return await _service().mark_started(*args, **kwargs)

    @staticmethod
    async def persist_projection(*args: Any, **kwargs: Any) -> Any:
        return await _service().persist_projection(*args, **kwargs)

    @staticmethod
    async def mark_waiting_approval(*args: Any, **kwargs: Any) -> Any:
        return await _service().mark_waiting_approval(*args, **kwargs)

    @staticmethod
    async def mark_resumed(*args: Any, **kwargs: Any) -> Any:
        return await _service().mark_resumed(*args, **kwargs)

    @staticmethod
    async def mark_terminal(*args: Any, **kwargs: Any) -> Any:
        return await _service().mark_terminal(*args, **kwargs)

    @staticmethod
    async def collect_partial_output(*args: Any, **kwargs: Any) -> Any:
        return await _service().collect_partial_output(*args, **kwargs)


class ExecutorPort:
    @staticmethod
    def validate_followup(*args: Any, **kwargs: Any) -> Any:
        return _executor().validate_followup(*args, **kwargs)

    @staticmethod
    def send_message(*args: Any, **kwargs: Any) -> Any:
        return _executor().send_message(*args, **kwargs)

    @staticmethod
    def submit_decisions(*args: Any, **kwargs: Any) -> Any:
        return _executor().submit_decisions(*args, **kwargs)

    @staticmethod
    def cancel(*args: Any, **kwargs: Any) -> Any:
        return _executor().cancel(*args, **kwargs)

    @staticmethod
    def subscribe_run_events(*args: Any, **kwargs: Any) -> Any:
        return _executor().subscribe_run_events(*args, **kwargs)

    @staticmethod
    def unsubscribe_run_events(*args: Any, **kwargs: Any) -> Any:
        return _executor().unsubscribe_run_events(*args, **kwargs)

    @staticmethod
    def get_run_event_history(*args: Any, **kwargs: Any) -> Any:
        return _executor().get_run_event_history(*args, **kwargs)


__all__ = [
    "ExecutorPort",
    "SubagentSessionPort",
    "configure_executor_port",
    "configure_service_port",
]
