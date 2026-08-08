"""Agent construction + checkpointer lifecycle for Noesis CLI.

Reuses Agent classes (SuperAgent/GeneralQAAgent/SimpleMCPAgent) with an
in-memory MemorySaver via temporary_checkpointer — same pattern as
evals/bootstrap.py:eval_runtime, but self-contained (no evals import).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from noesis.agents.common_qa import GeneralQAAgent
from noesis.agents.simple_mcp import SimpleMCPAgent
from noesis.agents.super_agent import SuperAgent
from noesis.config.checkpointer import temporary_checkpointer

QA_TYPE_MAP: dict[str, type] = {
    "super": SuperAgent,
    "super_agent": SuperAgent,
    "common": GeneralQAAgent,
    "common_qa": GeneralQAAgent,
    "simple_mcp": SimpleMCPAgent,
}

#: qa_type CLI 名 → Agent run_agent 的 qa_type 参数（None 表示不传）
_QA_TYPE_ENUM = {
    "super": "SUPER_AGENT_QA",
    "super_agent": "SUPER_AGENT_QA",
    "common": "COMMON_QA",
    "common_qa": "COMMON_QA",
    "simple_mcp": None,
}


def resolve_agent_class(qa_type: str) -> type:
    cls = QA_TYPE_MAP.get(qa_type)
    if cls is None:
        raise ValueError(f"unknown qa_type: {qa_type!r}; valid: {list(QA_TYPE_MAP)}")
    return cls


class ChatSession:
    """多轮会话:持有 MemorySaver + thread_id,跨 turn 复用。"""

    def __init__(self, *, qa_type: str, model_id: str | None, thread_id: str | None = None) -> None:
        self.qa_type = qa_type
        self.model_id = model_id
        self.thread_id = thread_id or f"cli-{uuid.uuid4().hex[:12]}"
        self.user_id = "cli-user"
        self.checkpointer = MemorySaver()
        self.agent = resolve_agent_class(qa_type)()

    def enter_context(self):
        """进入 temporary_checkpointer 上下文,注入 in-memory checkpointer。

        必须包住所有 run_turn 调用;Agent 内部 self.checkpointer 读此 ContextVar。
        """
        return temporary_checkpointer(self.checkpointer)

    async def run_turn(self, query: str) -> AsyncGenerator[dict[str, Any], None]:
        """单轮对话:调 agent.run_agent,yield 事件 dict。"""
        async for event in _run_agent_turn(
            agent=self.agent,
            query=query,
            thread_id=self.thread_id,
            user_id=self.user_id,
            model_id=self.model_id,
            qa_type=self.qa_type,
        ):
            yield event


async def _run_agent_turn(
    *,
    agent: Any,
    query: str,
    thread_id: str,
    user_id: str,
    model_id: str | None,
    qa_type: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """调用 agent.run_agent(),签名因 Agent 类而异。

    - SimpleMCPAgent: 只接受 query, session_id
    - GeneralQAAgent: 额外传 kb_search_enabled=False 跳过 Qdrant
    - SuperAgent: 传 db=None 跳过平台 DB 依赖
    """
    qa_enum = _QA_TYPE_ENUM.get(qa_type)
    if isinstance(agent, SimpleMCPAgent):
        agen = agent.run_agent(query, session_id=thread_id)
    elif isinstance(agent, GeneralQAAgent):
        agen = agent.run_agent(
            query,
            session_id=thread_id,
            current_user=SimpleNamespace(user_id=user_id),
            model_id=model_id,
            db=None,
            kb_search_enabled=False,
            web_search_enabled=True,
            qa_type=qa_enum,
        )
    else:  # SuperAgent
        agen = agent.run_agent(
            query,
            session_id=thread_id,
            current_user=SimpleNamespace(user_id=user_id),
            model_id=model_id,
            db=None,
            qa_type=qa_enum,
        )
    async for event in agen:
        yield event
