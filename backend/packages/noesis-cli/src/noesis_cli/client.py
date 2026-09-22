"""Agent construction + checkpointer lifecycle for Noesis CLI.

Reuses Agent classes (SuperAgent/GeneralQAAgent) with an
in-memory MemorySaver via temporary_checkpointer — same pattern as
evals/bootstrap.py:eval_runtime, but self-contained (no evals import).
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from noesis.agents.common_qa import GeneralQAAgent
from noesis.agents.super_agent import SuperAgent
from noesis.config.checkpointer import temporary_checkpointer

QA_TYPE_MAP: dict[str, type] = {
    "super": SuperAgent,
    "super_agent": SuperAgent,
    "common": GeneralQAAgent,
    "common_qa": GeneralQAAgent,
}

#: qa_type CLI 名 → Agent run_agent 的 qa_type 参数（None 表示不传）
_QA_TYPE_ENUM = {
    "super": "SUPER_AGENT_QA",
    "super_agent": "SUPER_AGENT_QA",
    "common": "COMMON_QA",
    "common_qa": "COMMON_QA",
}


def resolve_agent_class(qa_type: str) -> type:
    cls = QA_TYPE_MAP.get(qa_type)
    if cls is None:
        raise ValueError(f"unknown qa_type: {qa_type!r}; valid: {list(QA_TYPE_MAP)}")
    return cls


def current_user_id() -> str:
    """当前用户：NOESIS_USER_ID 覆盖，缺省本地默认。"""
    return os.environ.get("NOESIS_USER_ID", "").strip() or "cli-user"


def wire_langfuse() -> None:
    """LANGFUSE_* 凭据存在时绑定观测回调（host 实现在 backend server 包）。

    Agent 流式路径（noesis/runtime/stream.py）的 Langfuse 注入按 deps 开关
    门控，CLI 进程不经过 FastAPI lifespan，必须自行做与 server/wiring.py
    相同的绑定，否则逐 LLM/工具调用 trace 静默丢失。

    console script 的 sys.path 不含仓库 backend 目录（驱动方 driver 以
    PYTHONPATH 兜底，直接 shell 跑没有）——`import server` 会 ImportError，
    曾被静默吞掉造成「凭据齐全却无 trace」的摄入链路误诊；此处自动补
    backend 根目录后再导入，仍失败则大声警告。独立安装（无 server 包）
    时静默跳过。
    """
    if not (
        os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
        and os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    ):
        return
    try:
        from server.langfuse import sync_langfuse_env_from_app_config
        from server.wiring import wire_runtime_observability
    except ImportError:
        import sys
        from pathlib import Path

        backend_root = Path(__file__).resolve().parents[4]
        if backend_root.is_dir():
            sys.path.insert(0, str(backend_root))
        try:
            from server.langfuse import sync_langfuse_env_from_app_config
            from server.wiring import wire_runtime_observability
        except ImportError:
            logging.getLogger(__name__).warning(
                "LANGFUSE_* 凭据已配置但 server 包不可导入，本进程 Langfuse "
                "tracing 不生效（独立安装预期行为）"
            )
            return
    sync_langfuse_env_from_app_config()
    wire_runtime_observability()


def apply_env_model_direct(model_id: str | None) -> str | None:
    """env 直连模式（Claude Code 式）：NOESIS_API_KEY + NOESIS_BASE_URL
    提供端点与凭据时直接构造运行时快照，跳过目录/用户模型解析。

    返回生效的模型名（env 直连时为 wire 名）。必须在实际调用 LLM 前、
    同一线程上下文里调用（快照走 ContextVar）。
    """
    api_key = os.environ.get("NOESIS_API_KEY", "").strip()
    base_url = os.environ.get("NOESIS_BASE_URL", "").strip()
    if not api_key or not base_url:
        return model_id
    from noesis.llm.runtime_snapshot import RuntimeModelSnapshot, set_runtime_model_snapshots

    wire_name = (model_id or os.environ.get("NOESIS_MODEL", "")).strip()
    if not wire_name:
        raise ValueError(
            "env 直连模式需要模型名：--model 或 NOESIS_MODEL 至少一个"
        )
    model_type = os.environ.get("NOESIS_MODEL_TYPE", "").strip() or "openai"
    # id 必须等于实际传入 get_llm 的 model_id：快照查找按 id 全等匹配
    # （含子 Agent 线程的 replay 同款语义），对不上会回退目录解析报错
    base = dict(
        id=wire_name,
        provider_id="env",
        model_type=model_type,
        base_url=base_url,
        api_key=api_key,
        label="env-direct",
        wire_name=wire_name,
    )
    set_runtime_model_snapshots([
        RuntimeModelSnapshot(purpose="chat", **base),
        RuntimeModelSnapshot(purpose="summarization", **base),
    ])
    return wire_name


class ChatSession:
    """多轮会话:持有 MemorySaver + thread_id,跨 turn 复用。"""

    def __init__(
        self,
        *,
        qa_type: str,
        model_id: str | None,
        thread_id: str | None = None,
        kb_collections: list[str] | None = None,
        web_search_enabled: bool = True,
    ) -> None:
        self.qa_type = qa_type
        self.model_id = model_id
        self.thread_id = thread_id or f"cli-{uuid.uuid4().hex[:12]}"
        self.user_id = current_user_id()
        self.kb_collections = [c.strip() for c in kb_collections or [] if c.strip()]
        self.web_search_enabled = web_search_enabled
        self._kb_ready = False
        self.checkpointer = MemorySaver()
        self.agent = resolve_agent_class(qa_type)()

    def enter_context(self):
        """进入 temporary_checkpointer 上下文,注入 in-memory checkpointer。

        必须包住所有 run_turn 调用;Agent 内部 self.checkpointer 读此 ContextVar。
        CLI 是组合根:SuperAgent 的子会话操作走 agents.background.ports,
        此处一并注册服务侧实现(幂等)。
        """
        from noesis.services.runtime_ports import register_runtime_ports

        register_runtime_ports()
        return temporary_checkpointer(self.checkpointer)

    async def run_turn(
        self, query: str, *, enabled_skills: list[str] | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """单轮对话:调 agent.run_agent,yield 事件 dict。"""
        if self.kb_collections and not self._kb_ready:
            from noesis.knowledge.runtime import init_knowledge_base

            if not await init_knowledge_base():
                raise RuntimeError("Qdrant 不可用,无法进行知识库检索")
            self._kb_ready = True
        async for event in _run_agent_turn(
            agent=self.agent,
            query=query,
            thread_id=self.thread_id,
            user_id=self.user_id,
            model_id=self.model_id,
            qa_type=self.qa_type,
            enabled_skills=enabled_skills,
            kb_collections=self.kb_collections,
            web_search_enabled=self.web_search_enabled,
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
    enabled_skills: list[str] | None = None,
    kb_collections: list[str] | None = None,
    web_search_enabled: bool = True,
) -> AsyncGenerator[dict[str, Any], None]:
    """调用 agent.run_agent(),签名因 Agent 类而异。

    - GeneralQAAgent: kb_collections 提供即启用 KB 检索（默认关,跳过 Qdrant）
    - SuperAgent: 传 db=None 跳过平台 DB 依赖；enabled_skills 仅 SuperAgent 支持
    """
    qa_enum = _QA_TYPE_ENUM.get(qa_type)
    if isinstance(agent, GeneralQAAgent):
        agen = agent.run_agent(
            query,
            session_id=thread_id,
            current_user=SimpleNamespace(user_id=user_id),
            model_id=model_id,
            db=None,
            kb_search_enabled=bool(kb_collections),
            kb_collections=kb_collections or [],
            web_search_enabled=web_search_enabled,
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
            enabled_skills=enabled_skills,
        )
    async for event in agen:
        yield event
