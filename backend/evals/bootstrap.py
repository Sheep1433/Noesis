"""离线评测运行时依赖初始化（不走 FastAPI lifespan）。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.memory import MemorySaver

from noesis.config.checkpointer import temporary_checkpointer
from noesis.runtime.deps import temporary_attachment_service


class _NoAttachments:
    async def session_has_attachments(self, *_args: object, **_kwargs: object) -> bool:
        return False


@asynccontextmanager
async def eval_runtime(*, no_attachments: bool = False) -> AsyncIterator[MemorySaver]:
    """Use an in-memory checkpointer without importing platform services.

    SuperAgent benchmarks can opt into a scoped no-attachment provider. Harbor uses
    the bare factory and needs no platform capability bindings at all.
    """
    checkpointer = MemorySaver()
    with temporary_checkpointer(checkpointer):
        if no_attachments:
            with temporary_attachment_service(_NoAttachments()):
                yield checkpointer
        else:
            yield checkpointer


@asynccontextmanager
async def agentic_rag_runtime() -> AsyncIterator[None]:
    """Initialize the KB engine + Postgres storage for core RAG tools.

    KB retrieval, Qdrant, VLM, and collection-config are imported directly by
    core tools from ``noesis.knowledge`` / ``noesis.repositories`` /
    ``noesis.storage``; no dependency injection is needed. This context only
    ensures Qdrant is connected and the Postgres engine is ready for the
    synchronous collection-config reads performed inside Agent tool threads.
    """
    from noesis.knowledge.runtime import close_knowledge_base, init_knowledge_base
    from noesis.storage.postgres.manager import pg_manager

    if not await init_knowledge_base():
        raise RuntimeError("Agentic RAG 评测需要可用的 Qdrant")
    pg_manager._ensure_engine()
    try:
        yield
    finally:
        await close_knowledge_base()
        await pg_manager.close()


async def resolve_user_model(user_id: str, model_id: str) -> "list":
    """解析用户自定义模型为 runtime snapshot 列表（含解密 key），不注入 ContextVar。

    user_id 接受用户名或 uuid。未命中时抛错：离线评测拒绝静默回退内置
    目录——那会让被评/judge 分离与成本核算全部失真。
    使用一次性 engine（asyncpg 池绑定创建时的 loop，同步 CLI 多次
    asyncio.run 复用全局池会炸）。
    """
    from noesis.services.user_llm_service import UserLLMService
    from noesis.storage.postgres.manager import ASYNC_SQLALCHEMY_DATABASE_URL
    from noesis.storage.postgres.models.auth import TUser
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(ASYNC_SQLALCHEMY_DATABASE_URL)
    try:
        async with async_sessionmaker(bind=engine, expire_on_commit=False)() as db:
            # 用户名 → t_user.id（uuid 直通）
            normalized_user = str(user_id).strip()
            try:
                import uuid as _uuid
                _uuid.UUID(normalized_user)
            except ValueError:
                row = (await db.execute(
                    select(TUser.id).where(TUser.username == normalized_user))).first()
                if row is None:
                    raise ValueError(f"用户不存在: {normalized_user!r}")
                normalized_user = str(row[0])
            snapshots = await UserLLMService.resolve_runtime_snapshots(
                db, user_id=normalized_user, model_id=model_id)
    finally:
        await engine.dispose()
    if not snapshots:
        raise ValueError(
            f"用户 {user_id} 无自定义模型 {model_id!r}（拒绝静默回退内置目录；"
            f"检查 --model-user / --model-id）"
        )
    return snapshots


def bind_snapshots(snapshots: "list", *, include_summarization: bool = False) -> str:
    """在当前线程上下文注入模型快照；返回 snapshot id（后续 get_llm 应使用返回值）。

    include_summarization：同时以 summarization purpose 注入同一模型
    （压缩评测的摘要引擎走 get_llm(purpose="summarization")）。
    """
    import dataclasses

    from noesis.llm.runtime_snapshot import set_runtime_model_snapshots

    bound = list(snapshots)
    if include_summarization and snapshots:
        bound.append(dataclasses.replace(snapshots[0], purpose="summarization"))
    set_runtime_model_snapshots(bound)
    return snapshots[0].id


async def bind_user_model(
    user_id: str, model_id: str, *, include_summarization: bool = False
) -> str:
    """异步调用方一步到位（在当前协程上下文注入，注意不要包在 asyncio.run 里）。"""
    return bind_snapshots(
        await resolve_user_model(user_id, model_id),
        include_summarization=include_summarization,
    )


def bind_user_model_sync(
    user_id: str, model_id: str, *, include_summarization: bool = False
) -> str:
    """同步调用方专用：解析与注入都在主线程上下文完成。

    禁止用 asyncio.run 包 bind_user_model 代替本函数——任务内的
    ContextVar 修改不会传回调用方，快照会静默丢失。
    """
    return bind_snapshots(
        asyncio.run(resolve_user_model(user_id, model_id)),
        include_summarization=include_summarization,
    )
