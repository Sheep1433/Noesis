"""扫描已安装 skill 包，供 /skills 命令与（D 类）skill 命令 fallback 共用。

/skills 列表与 skill 快捷命令 SHALL 走同一份扫描结果，避免「列表里有但调不通」。
"""
from __future__ import annotations

import re
from pathlib import Path

from noesis.config.extensions_paths import skills_root

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)
_DESC_MAX = 80


def _parse_frontmatter(text: str) -> dict[str, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip().strip("\"'")
        if val:
            fields[key.strip()] = val
    return fields


def _short(desc: str) -> str:
    desc = desc.strip()
    if len(desc) <= _DESC_MAX:
        return desc
    return desc[: _DESC_MAX - 1].rstrip() + "…"


def scan_installed_skills() -> list[tuple[str, str]]:
    """返回 [(name, 简短描述)]，按 name 字典序。无 SKILL.md 或无 name 的目录跳过。"""
    root = skills_root()
    if not root.exists():
        return []
    out: list[tuple[str, str]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fields = _parse_frontmatter(text)
        name = fields.get("name") or child.name
        desc = _short(fields.get("description", ""))
        out.append((name, desc))
    return out
