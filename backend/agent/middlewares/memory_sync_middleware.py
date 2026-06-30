"""每次模型调用前从磁盘重载 `/memory/*`，保证 edit_file 后同会话可见。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol


class MemorySyncMiddleware(AgentMiddleware[AgentState]):
    """在 `before_model` 重载 memory sources，配合 `MemoryMiddleware` 使用。"""

    def __init__(self, *, backend: BackendProtocol, sources: list[str]) -> None:
        super().__init__()
        self._backend = backend
        self._sources = list(sources)

    def _reload(self) -> dict[str, str] | None:
        if not self._sources:
            return None
        contents: dict[str, str] = {}
        results = self._backend.download_files(self._sources)
        for path, response in zip(self._sources, results, strict=True):
            if response.error is not None:
                if response.error == "file_not_found":
                    continue
                raise ValueError(f"Failed to download {path}: {response.error}")
            if response.content is not None:
                contents[path] = response.content.decode("utf-8")
        return contents

    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        if "memory_contents" not in state:
            return None
        contents = self._reload()
        if contents is None:
            return None
        return {"memory_contents": contents}

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        if "memory_contents" not in state:
            return None
        contents = await asyncio.to_thread(self._reload)
        if contents is None:
            return None
        return {"memory_contents": contents}
