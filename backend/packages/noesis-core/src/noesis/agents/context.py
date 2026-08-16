"""Agent prompt and memory source resolver shared by runtime and read-only preview."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from noesis.agents.backends.paths import AGENT_MEMORY_AGENTS_FILE, AGENT_MEMORY_USER_FILE
from noesis.config.user_data_paths import ensure_user_memory_files, get_user_agents_md_path, get_user_profile_md_path
from noesis.agents.prompts import PromptProfile, build_prompt


@dataclass(frozen=True)
class ContextSource:
    id: str
    label: str
    injected: bool
    characters: int
    token_estimate: int
    content: str


@dataclass(frozen=True)
class ResolvedAgentContext:
    profile: str
    system_prompt: str
    memory_sources: tuple[str, ...]
    sources: tuple[ContextSource, ...]
    compiled_content: str

    def public_view(self) -> dict:
        return {
            "profile": self.profile,
            "sources": [asdict(source) for source in self.sources],
            "compiled_content": self.compiled_content,
            "characters": len(self.compiled_content),
            "token_estimate": max(1, len(self.compiled_content) // 4),
        }


class ContextResolver:
    MEMORY_PROFILES = frozenset({PromptProfile.SUPER_AGENT.value})

    @classmethod
    def resolve(cls, user_id: str | int, profile: PromptProfile | str) -> ResolvedAgentContext:
        key = profile.value if isinstance(profile, PromptProfile) else str(profile)
        prompt = build_prompt(key)
        memory_enabled = key in cls.MEMORY_PROFILES
        memory_sources: tuple[str, ...] = (AGENT_MEMORY_USER_FILE, AGENT_MEMORY_AGENTS_FILE) if memory_enabled else ()
        sources = [cls._source("system", "Agent 规则", True, prompt)]
        if memory_enabled:
            ensure_user_memory_files(user_id)
            sources.extend([
                cls._file_source("profile", "用户画像", get_user_profile_md_path(user_id)),
                cls._file_source("memory", "长期记忆", get_user_agents_md_path(user_id)),
            ])
        compiled = "\n\n".join(f"## {source.label}\n{source.content}" for source in sources if source.injected and source.content.strip())
        return ResolvedAgentContext(key, prompt, memory_sources, tuple(sources), compiled)

    @staticmethod
    def _source(source_id: str, label: str, injected: bool, content: str) -> ContextSource:
        return ContextSource(source_id, label, injected, len(content), max(1, len(content) // 4), content)

    @classmethod
    def _file_source(cls, source_id: str, label: str, path: Path) -> ContextSource:
        content = path.read_text(encoding="utf-8") if path.is_file() else ""
        return cls._source(source_id, label, bool(content.strip()), content)
