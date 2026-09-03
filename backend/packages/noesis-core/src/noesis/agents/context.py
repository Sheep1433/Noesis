"""Agent prompt and memory source resolver shared by runtime and read-only preview."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from noesis.agents.backends.memory import UserMemoryBackend
from noesis.agents.backends.paths import (
    AGENT_MEMORY_AGENTS_FILE,
    AGENT_MEMORY_INDEX_FILE,
    AGENT_MEMORY_USER_FILE,
)
from noesis.config.user_data_paths import (
    ensure_user_memory_files,
    get_user_agents_md_path,
    get_user_memory_index_path,
    get_user_profile_md_path,
)
from noesis.agents.prompts import PromptProfile, build_prompt
from noesis.agents.prompts.memory import NOESIS_MEMORY_SYSTEM_PROMPT


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


def render_memory_block(user_id: str | int, memory_sources: tuple[str, ...]) -> str:
    """按运行时同一渲染链产出记忆段（<agent_memory> 包裹 + 路径头 +
    guidelines；HTML 注释剥离），供预览与真实注入共用格式。

    deepagents 0.6.12 的 ``MemoryMiddleware._format_agent_memory`` 是该格式
    的唯一实现；经实例调用而非复制，防两套格式漂移。读取经 UserMemoryBackend
    （键剥 /memory 路由前缀，与 Composite 运行时派发一致），渲染仍以完整
    source 路径为键——与真实注入的路径头逐字一致。
    """
    from deepagents.middleware.memory import MemoryMiddleware

    from noesis.agents.backends.paths import AGENT_MEMORY_ROUTE

    backend = UserMemoryBackend(
        agents_path=get_user_agents_md_path(user_id),
        user_path=get_user_profile_md_path(user_id),
        user_id=str(user_id),
    )
    middleware = MemoryMiddleware(
        backend=backend,
        sources=list(memory_sources),
        system_prompt=NOESIS_MEMORY_SYSTEM_PROMPT,
    )
    stripped = [source.removeprefix(AGENT_MEMORY_ROUTE) for source in memory_sources]
    contents: dict[str, str] = {}
    responses = backend.download_files(stripped)
    for source, response in zip(memory_sources, responses, strict=True):
        if response.error is None and response.content is not None:
            contents[source] = response.content.decode("utf-8")
    return middleware._format_agent_memory(contents, NOESIS_MEMORY_SYSTEM_PROMPT)  # noqa: SLF001


class ContextResolver:
    MEMORY_PROFILES = frozenset({PromptProfile.SUPER_AGENT.value})

    @classmethod
    def resolve(cls, user_id: str | int, profile: PromptProfile | str) -> ResolvedAgentContext:
        key = profile.value if isinstance(profile, PromptProfile) else str(profile)
        prompt = build_prompt(key)
        memory_enabled = key in cls.MEMORY_PROFILES
        memory_sources: tuple[str, ...] = (
            AGENT_MEMORY_USER_FILE, AGENT_MEMORY_AGENTS_FILE, AGENT_MEMORY_INDEX_FILE
        ) if memory_enabled else ()
        sources = [cls._source("system", "Agent 规则", True, prompt)]
        if memory_enabled:
            ensure_user_memory_files(user_id)
            sources.extend([
                cls._file_source("profile", "用户画像", get_user_profile_md_path(user_id)),
                cls._file_source("memory", "长期记忆", get_user_agents_md_path(user_id)),
                cls._file_source("memory-index", "记忆索引", get_user_memory_index_path(user_id)),
            ])
        # compiled = 模型真实所见（system prompt + 记忆块，与 append_to_system_message
        # 相同的 \n\n 拼接）；来源明细仅供 UI 分段展示
        compiled = prompt
        if memory_enabled:
            compiled = f"{prompt}\n\n{render_memory_block(user_id, memory_sources)}"
        return ResolvedAgentContext(key, prompt, memory_sources, tuple(sources), compiled)

    @staticmethod
    def _source(source_id: str, label: str, injected: bool, content: str) -> ContextSource:
        return ContextSource(source_id, label, injected, len(content), max(1, len(content) // 4), content)

    @classmethod
    def _file_source(cls, source_id: str, label: str, path: Path) -> ContextSource:
        content = path.read_text(encoding="utf-8") if path.is_file() else ""
        return cls._source(source_id, label, bool(content.strip()), content)
