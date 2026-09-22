"""组合根端口注册：确保 ``agents.background.ports`` 的实现就绪。

端口实现靠各服务模块 import 时自注册（模块尾 ``configure_*_port``）。
server 与 evals 等组合根在启动时调用 ``register_runtime_ports``（幂等），
保证首个请求 / 后台任务前端口可用；漏配的进程会在首次端口调用时以
RuntimeError 快速失败（文案指明是端口未注册）。
"""

from __future__ import annotations


def register_runtime_ports() -> None:
    """import 各实现模块即完成注册（幂等，重复调用无副作用）。"""
    from noesis.services.chat_service import ChatService  # noqa: F401
    from noesis.services.subagent_session_service import (  # noqa: F401
        SubagentSessionService,
    )
    from noesis.services.bg_continuation_service import (  # noqa: F401
        schedule_maybe_continue,
    )
    from noesis.services.bg_notification_store import (  # noqa: F401
        BgNotificationStore,
    )


__all__ = ["register_runtime_ports"]
