"""端口契约：实现签名必须与 ports.py 的转发签名逐参匹配。

逐项比对参数名 / 种类 / 默认值有无（注解一致性交给类型检查器与 IDE）；
任何一侧出现 ``*args/**kwargs`` 万能管道、或签名漂移，本测试即红。
配对关系：端口转发类 ↔ 实现解析器——类实现经属性访问解出方法，模块级
函数实现（Continuation）就是方法本身，Executor 门面的真实来源是任务类
方法与 jobs/events 模块函数的组合（两跳都钉）。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import pytest

from noesis.agents.background import ports
from noesis.agents.background.executor import BackgroundTaskExecutor, _ExecutorRuntimePort
from noesis.services import bg_notification_store, bg_shell_job_service
from noesis.services.bg_continuation_service import schedule_maybe_continue
from noesis.services.chat_service import ChatService
from noesis.services.subagent_session_service import SubagentSessionService


def _shape(func) -> list[tuple[str, inspect._ParameterKind, bool]]:
    """签名形状：[(参数名, 种类, 是否有默认值)]，跳过 self/cls。"""
    sig = inspect.signature(func)
    shape: list[tuple[str, inspect._ParameterKind, bool]] = []
    for name, p in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        assert p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ), f"{func!r} 仍使用 *args/**kwargs 万能管道"
        shape.append((name, p.kind, p.default is not inspect.Parameter.empty))
    return shape


def _cls_impl(cls):
    return lambda name: getattr(cls, name)


def _executor_impl(name: str):
    if hasattr(BackgroundTaskExecutor, name):
        return getattr(BackgroundTaskExecutor, name)
    from noesis.agents.background.jobs import events

    return getattr(events, name)


# (端口转发类, 实现解析器)
_PAIRS = [
    (ports.SubagentSessionPort, _cls_impl(SubagentSessionService)),
    (ports.SessionOpsPort, _cls_impl(ChatService)),
    # 两跳都钉：门面（注册的实现）与其真实组合来源（任务类 + 事件模块）
    (ports.ExecutorPort, _cls_impl(_ExecutorRuntimePort)),
    (ports.ExecutorPort, _executor_impl),
    (ports.ContinuationPort, lambda _name: schedule_maybe_continue),
    (
        ports.NotificationStorePort,
        _cls_impl(bg_notification_store.BgNotificationStore),
    ),
    (ports.ShellJobPort, _cls_impl(bg_shell_job_service.BgShellJobService)),
]


@pytest.mark.parametrize(
    "port_cls, impl_resolver",
    _PAIRS,
    ids=lambda p: p[0].__name__ if isinstance(p, tuple) else "",
)
def test_impl_matches_port(port_cls, impl_resolver: Callable[[str], object]) -> None:
    port_methods = [name for name in port_cls.__dict__ if not name.startswith("_")]
    assert port_methods, f"{port_cls.__name__} 无公开转发方法"
    for name in port_methods:
        port_shape = _shape(getattr(port_cls, name))
        impl_shape = _shape(impl_resolver(name))
        assert port_shape == impl_shape, (
            f"{port_cls.__name__}.{name} 的实现签名与转发签名不符："
            f"{port_shape} != {impl_shape}"
        )
