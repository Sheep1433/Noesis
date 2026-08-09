"""一次 Agent run 内固定的用户模型解析快照。"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeModelSnapshot:
    id: str
    provider_id: str
    purpose: str
    model_type: str
    model_name: str
    base_url: str
    api_key: str


_snapshots: ContextVar[dict[str, RuntimeModelSnapshot]] = ContextVar(
    "noesis_runtime_model_snapshots", default={}
)


def set_runtime_model_snapshot(snapshot: RuntimeModelSnapshot | None) -> None:
    _snapshots.set({snapshot.purpose: snapshot} if snapshot is not None else {})


def set_runtime_model_snapshots(snapshots: list[RuntimeModelSnapshot]) -> None:
    """替换当前 run 的全部用途快照，避免跨轮复用旧绑定。"""
    _snapshots.set({snapshot.purpose: snapshot for snapshot in snapshots})


def get_runtime_model_snapshot(
    model_id: str | None = None,
    *,
    purpose: str | None = None,
) -> RuntimeModelSnapshot | None:
    current = _snapshots.get()
    if model_id:
        return next((item for item in current.values() if item.id == model_id), None)
    return current.get(purpose or "chat")
