"""命令层运行时依赖注入点。

命令 handler 位于 ``noesis.chat`` 包，受包边界约束 SHALL NOT 直接 import
``noesis.services`` / ``noesis.agents``。需要运行时单例（如 run_manager）时，
由 server wiring 在启动时通过 ``set_run_manager_provider`` 注入；未注入则降级。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

_run_manager_provider: Optional[Callable[[], Any]] = None


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
