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
    base_url: str
    api_key: str
    label: str = ""
    context_window: int = 0
    # 发给端点的真实模型名；自定义模型 id 为复合「slug/model_id」身份，
    # 线上名与选择器身份分离（对齐 dsh route/model 二段身份）
    wire_name: str = ""


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


def replay_runtime_model_snapshot(
    snapshot: RuntimeModelSnapshot | None, *, target_model_id: str | None
) -> None:
    """跨线程/隔离 loop 重放父上下文解析出的快照。

    ContextVar 不跨线程：子 Agent 的隔离线程里 get_llm 看不到父 run 的
    快照，自定义模型会经目录解析静默回退平台默认（2026-09-03：子 Agent
    配置 glm 实际跑 kilo）。父侧捕获纯数据快照、隔离线程开局重放。

    仅当快照身份与本次目标 model_id 一致时重放——不一致（followup 按
    turn 覆盖到别的模型）时保持空，让解析按 strict 语义大声失败，绝不
    用错模型。
    """
    if snapshot is None:
        return
    if target_model_id and snapshot.id != target_model_id:
        return
    set_runtime_model_snapshot(snapshot)
