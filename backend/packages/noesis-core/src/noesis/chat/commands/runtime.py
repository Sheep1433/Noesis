"""命令层运行时依赖注入点。

命令 handler 位于 ``noesis.chat`` 包，受包边界约束 SHALL NOT 直接 import
``noesis.services`` / ``noesis.agents``。需要运行时单例（如 run_manager）或
需要触达 service 层的能力（如建新会话）时，由 server wiring 在启动时通过
对应的 ``set_*_provider`` 注入；未注入则降级。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional, Protocol


class ManualCompactionResult(Protocol):
    status: str
    pre_message_count: int
    post_message_count: int

_run_manager_provider: Optional[Callable[[], Any]] = None
ManualCompactionProvider = Callable[
    [str, str, str | None], Awaitable[ManualCompactionResult]
]
_compaction_provider: Optional[ManualCompactionProvider] = None


def set_run_manager_provider(provider: Callable[[], Any]) -> None:
    """由 server wiring 调用，注入 run_manager 访问器。"""
    global _run_manager_provider
    _run_manager_provider = provider


def get_run_manager() -> Any:
    """返回 run_manager 单例；未注入或不可用时返回 None（命令优雅降级）。"""
    if _run_manager_provider is None:
        return None
    try:
        return _run_manager_provider()
    except Exception:  # noqa: BLE001 —— CLI/轻量环境可能缺少 DB 依赖
        return None


def set_compaction_provider(
    provider: ManualCompactionProvider,
) -> None:
    """Inject the host-level manual compaction use case."""
    global _compaction_provider
    _compaction_provider = provider


def get_compaction_provider() -> Optional[ManualCompactionProvider]:
    """Return the manual compaction use case, if the host wired it."""
    return _compaction_provider


#: 注入的会话工厂：``async (user_id, title=None, parent_id=None) -> new_session_id``。
#: 由 server wiring 包装 ``ChatService.create_session`` 注入；未注入时 handler 降级。
_session_factory_provider: Optional[Callable[[], Any]] = None


def set_session_factory_provider(provider: Callable[[], Any]) -> None:
    """由 server wiring 调用，注入「建新会话」工厂访问器。"""
    global _session_factory_provider
    _session_factory_provider = provider


def get_session_factory() -> Any:
    """返回注入的 session 工厂 callable；未注入返回 None（命令优雅降级）。

    工厂约定签名：``async (user_id: str, title: str | None = None,
    parent_id: str | None = None) -> str``（返回新 session_id）。
    """
    if _session_factory_provider is None:
        return None
    try:
        return _session_factory_provider()
    except Exception:  # noqa: BLE001 —— CLI/轻量环境可能缺少 DB 依赖
        return None
