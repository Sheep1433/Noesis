"""md 文件记忆层：类型定义（冻结五类）。"""

from __future__ import annotations

# 类型集冻结（openspec: md-memory-layer）：目录即枚举，新增类型需新变更提案。
MEMORY_TYPES: tuple[str, ...] = (
    "preference",
    "goal",
    "decision",
    "experience",
    "gotcha",
)

TYPE_LABELS: dict[str, str] = {
    "preference": "偏好",
    "goal": "目标",
    "decision": "决策",
    "experience": "经验",
    "gotcha": "注意事项",
}


def validate_memory_type(memory_type: str) -> str:
    if memory_type not in MEMORY_TYPES:
        raise ValueError(
            f"非法记忆类型: {memory_type!r}，仅允许 {'/'.join(MEMORY_TYPES)}"
        )
    return memory_type


__all__ = ["MEMORY_TYPES", "TYPE_LABELS", "validate_memory_type"]
