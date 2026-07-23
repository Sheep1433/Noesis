"""Skills 源目录与会话级过滤（供 SkillsMiddleware，非 filesystem backend）。"""

from __future__ import annotations

from pathlib import Path

from deepagents.middleware.skills import SkillSource

from agent.backends.paths import (
    AGENT_PERSONAL_SKILLS_ROUTE,
    AGENT_PUBLIC_SKILLS_ROUTE,
)
from config.extensions_paths import skills_root
from config.user_data_paths import get_user_skills_dir

SKILL_SOURCES: tuple[SkillSource, ...] = (
    (AGENT_PUBLIC_SKILLS_ROUTE, "Public"),
    (AGENT_PERSONAL_SKILLS_ROUTE, "Personal"),
)


def package_has_skill_md(root: Path, package: str) -> bool:
    pkg = root / package
    return pkg.is_dir() and (pkg / "SKILL.md").is_file()


def resolve_skill_sources_for_session(
    user_id: str | int,
    enabled_skills: list[str] | None,
) -> list[SkillSource]:
    if enabled_skills is None:
        return list(SKILL_SOURCES)

    names = [str(n).strip() for n in enabled_skills if str(n or "").strip()]
    if not names:
        return []

    platform_root = skills_root()
    user_root = get_user_skills_dir(user_id)
    sources: list[SkillSource] = []
    for name in names:
        if package_has_skill_md(platform_root, name):
            route = f"{AGENT_PUBLIC_SKILLS_ROUTE.rstrip('/')}/{name}/"
            sources.append((route, name))
        if package_has_skill_md(user_root, name):
            route = f"{AGENT_PERSONAL_SKILLS_ROUTE.rstrip('/')}/{name}/"
            sources.append((route, f"{name} (personal)"))
    return sources
