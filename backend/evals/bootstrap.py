"""离线评测运行时依赖初始化（不走 FastAPI lifespan）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.memory import MemorySaver

import noesis.config.checkpointer as checkpointer_module
from noesis.runtime.deps import temporary_attachment_service


class _NoAttachments:
    async def session_has_attachments(self, *_args: object, **_kwargs: object) -> bool:
        return False


@asynccontextmanager
async def eval_runtime(*, no_attachments: bool = False) -> AsyncIterator[None]:
    """Use an in-memory checkpointer without importing platform services.

    SuperAgent benchmarks can opt into a scoped no-attachment provider. Harbor uses
    the bare factory and needs no platform capability bindings at all.
    """
    previous = checkpointer_module._saver
    checkpointer_module._saver = MemorySaver()
    try:
        if no_attachments:
            with temporary_attachment_service(_NoAttachments()):
                yield
        else:
            yield
    finally:
        checkpointer_module._saver = previous
