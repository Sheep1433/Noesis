"""product-facing-copy-audit: 用户可见记忆文案不得泄露内部术语。

设计要求（openspec add-run-aware-memory-cortex design.md「API 与设置页」）：
用户文案使用业务词，不得显示数据库表、claim token、provider key、workspace
路径、Qdrant 或内部错误。本测试静态扫描前端设置区组件与后端记忆服务中的
用户可见文案（含 CJK 的字符串常量），作为 product-facing-copy-audit 的可复现实现。
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

_FRONTEND_SOURCES = [
    _ROOT / "../frontend/src/views/settings/sections/MemoryEditorSection.vue",
]

_BACKEND_SOURCES = [
    _ROOT
    / "packages/noesis-core/src/noesis/services/memory/management.py",
    _ROOT / "packages/noesis-core/src/noesis/services/memory/query.py",
    _ROOT / "packages/noesis-core/src/noesis/services/memory/preferences.py",
    _ROOT / "server/api/user_settings_api.py",
]

_FORBIDDEN_TOKENS = (
    "qdrant",
    "claim",
    "outbox",
    "t_memory",
    "postgres",
    "alembic",
    "provider",
    "embedding",
    "noesis/",
    "users/",
)

_HAS_CJK = lambda value: any("一" <= char <= "鿿" for char in value)


def _python_user_facing_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _HAS_CJK(node.value):
                literals.append(node.value)
    return literals


def test_backend_memory_copy_avoids_internal_terms() -> None:
    for path in _BACKEND_SOURCES:
        for literal in _python_user_facing_literals(path):
            lowered = literal.casefold()
            for token in _FORBIDDEN_TOKENS:
                assert token not in lowered, (
                    f"{path.name} 的用户文案包含内部术语 {token!r}: {literal!r}"
                )


def test_frontend_memory_copy_avoids_internal_terms() -> None:
    for path in _FRONTEND_SOURCES:
        text = path.read_text(encoding="utf-8").casefold()
        for token in _FORBIDDEN_TOKENS:
            assert token not in text, f"{path.name} 包含内部术语 {token!r}"
