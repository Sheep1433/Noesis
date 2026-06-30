"""从磁盘扫描 Skills 并生成 prompt 索引块（渐进披露：先索引，命中再 read_file）。"""

from __future__ import annotations

import re
from pathlib import Path

from config.extensions_paths import skills_root
from config.user_data_paths import get_user_skills_dir

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL | re.MULTILINE)
_FIELD_RE = re.compile(r"^([a-zA-Z0-9_-]+):\s*(.+)$", re.MULTILINE)


def _parse_skill_meta(skill_md: Path) -> tuple[str, str] | None:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    block = m.group(1)
    fields: dict[str, str] = {}
    for fm in _FIELD_RE.finditer(block):
        key, val = fm.group(1), fm.group(2).strip().strip('"').strip("'")
        fields[key] = val
    name = fields.get("name") or skill_md.parent.name
    description = fields.get("description") or ""
    return name, description


def _scan_skills_root(root: Path, *, route_prefix: str) -> list[tuple[str, str, str]]:
    if not root.is_dir():
        return []
    entries: list[tuple[str, str, str]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        meta = _parse_skill_meta(skill_md)
        if meta is None:
            continue
        name, description = meta
        agent_path = f"{route_prefix}{child.name}/SKILL.md"
        entries.append((name, description, agent_path))
    return entries


def build_skills_index_prompt(*, user_id: str | None = None) -> str:
    """生成 compact skills 索引；custom 与 extensions 同名时 custom 优先。"""
    by_name: dict[str, tuple[str, str]] = {}

    for name, desc, path in _scan_skills_root(
        skills_root(), route_prefix="/skills/extensions/"
    ):
        by_name[name] = (desc, path)

    if user_id:
        custom_root = Path(get_user_skills_dir(user_id))
        for name, desc, path in _scan_skills_root(
            custom_root, route_prefix="/skills/custom/"
        ):
            by_name[name] = (desc, path)

    if not by_name:
        return ""

    lines = [
        "<skills_index>",
        "回复前先扫下列 Skills；若任务明显匹配某 Skill，先 `read_file` 其 SKILL.md 再执行。",
        "未匹配时按通用推理与工具完成任务。",
        "",
    ]
    for name in sorted(by_name):
        desc, path = by_name[name]
        line = f"- **{name}** (`{path}`)"
        if desc:
            line += f"：{desc}"
        lines.append(line)
    lines.append("</skills_index>")
    return "\n".join(lines)
