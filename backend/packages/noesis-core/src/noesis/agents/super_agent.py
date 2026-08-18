"""SuperAgent - 通用超级智能体（filesystem + skills + web + 用户记忆）。"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncGenerator, Optional

from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.subagents import SubAgent
from langgraph.types import Command
from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.agents.backends import agent_sandbox_session, create_agent_backend
from noesis.agents.backends.paths import AGENT_MEMORY_AGENTS_FILE, AGENT_MEMORY_USER_FILE
from noesis.agents.base import BaseAgent, DEFAULT_RECURSION_LIMIT
from noesis.factory import build_noesis_middleware, create_noesis_agent
from noesis.agents.tools.ask_user import ask_user_tool, build_interrupt_on
from noesis.agents.prompts import PromptProfile, build_prompt
from noesis.agents.prompts.memory import NOESIS_MEMORY_SYSTEM_PROMPT
from noesis.agents.prompts.super_agent import NOESIS_SKILLS_SYSTEM_PROMPT
from noesis.agents.skills import resolve_skill_sources_for_session
from noesis.agents.tools import build_web_search_tools
from noesis.agents.tools.chat_attachment_tools import build_attachment_tools
from noesis.agents.tools.kb_search_tool import build_kb_search_tools
from noesis.agents.tools.memory_tools import build_memory_tools
from noesis.runtime.logging import logger
from noesis.config.env import ChatAttachmentConfig, HitlConfig
from noesis.config.user_data_paths import ensure_user_memory_files
from noesis.agents.context import ContextResolver
from noesis.llm.factory import get_llm
from noesis.runtime.deps import require_attachment_service
from noesis.runtime.attachments.input_resolver import AttachmentInputResolver

_MEMORY_SOURCES = [AGENT_MEMORY_USER_FILE, AGENT_MEMORY_AGENTS_FILE]


def _resolve_user_id(current_user) -> Optional[str]:
    if current_user is None:
        return None
    uid = getattr(current_user, "user_id", None)
    return str(uid) if uid is not None else None


def _build_task_worker_subagents(
    backend: BackendProtocol,
    tools: list,
    skill_sources: list,
    *,
    user_id: str,
    model_id: str | None = None,
    interrupt_on: dict | None = None,
    session_id: str = "",
) -> list[SubAgent]:
    model = get_llm(model_id=model_id)
    subagent_middleware = build_noesis_middleware(
        profile="SUBAGENT",
        model=model,
        model_id=model_id,
        tools=tools,
        backend=backend,
        skills=skill_sources,
        skills_user_id=user_id,
        skills_system_prompt=NOESIS_SKILLS_SYSTEM_PROMPT,
        session_id=session_id,
    )
    spec: SubAgent = {
        "name": "task-worker",
        "description": (
            "在独立上下文中完成主 Agent 委派的单个子任务：阅读 Skills、web_search/web_fetch、"
            "工作区读写与多步执行，只返回短结构化小结（长文落盘）。"
            "复杂任务应优先委派：多源检索、调研、批量读文件、实现与验证等，避免主上下文被工具原文撑满。"
        ),
        "system_prompt": build_prompt(PromptProfile.SUPER_AGENT_SUB),
        "model": model,
        "tools": tools,
        "middleware": subagent_middleware,
    }
    if interrupt_on:
        spec["interrupt_on"] = interrupt_on
    return [spec]


class SuperAgent(BaseAgent):
    """通用超级智能体。"""

    async def _create_compiled_agent(
        self,
        *,
        user_id: str,
        session_id: str,
        model_id: Optional[str],
        mcp_tools: Optional[list],
        enabled_skills: Optional[list[str]],
        file_list: dict | None,
        db: Optional[AsyncSession],
        kb_collections: Optional[list[str]] = None,
        kb_search_enabled: bool = True,
        disable_hitl: bool = False,
    ):
        ensure_user_memory_files(user_id)
        backend = await create_agent_backend(user_id, session_id)
        web_tools = build_web_search_tools()
        tools = list(web_tools) + list(mcp_tools or [])
        # KB 检索工具（用户勾选启用时挂载）
        if kb_search_enabled and kb_collections is not None:
            kb_tools = build_kb_search_tools(
                default_collection_names=kb_collections,
                enforce_scope=bool(kb_collections),
            )
            if kb_tools:
                tools.extend(kb_tools)
        if db is not None:
            tools.extend(build_memory_tools(user_id=user_id, db=db))
        interrupt_on = None
        # 无人值守场景（定时任务）禁用 HITL：不挂 ask_user、不设 interrupt_on，避免 agent 卡在等待审批。
        if HitlConfig.enabled and not disable_hitl:
            tools = tools + [ask_user_tool]
            interrupt_on = build_interrupt_on(session_id=session_id)
        skill_sources = resolve_skill_sources_for_session(user_id, enabled_skills)
        resolved_context = ContextResolver.resolve(user_id, PromptProfile.SUPER_AGENT)
        if (
            ChatAttachmentConfig.enabled
            and db is not None
            and session_id
            and user_id
            and await require_attachment_service().session_has_attachments(
                session_id=session_id,
                user_id=user_id,
                db=db,
                file_dict=file_list,
            )
        ):
            tools = tools + build_attachment_tools(
                session_id=session_id,
                user_id=user_id,
                db=db,
            )

        return create_noesis_agent(
            profile="SUPER_AGENT_QA",
            tools=tools,
            system_prompt=resolved_context.system_prompt,
            checkpointer=self.checkpointer,
            backend=backend,
            workspace="/workspace",
            session_id=session_id,
            attachments=tuple(str(name) for name in (file_list or {})),
            skills=skill_sources,
            skills_user_id=user_id,
            skills_system_prompt=NOESIS_SKILLS_SYSTEM_PROMPT,
            memory=resolved_context.memory_sources,
            memory_system_prompt=NOESIS_MEMORY_SYSTEM_PROMPT,
            todo=True,
                subagents=_build_task_worker_subagents(
                backend,
                tools,
                skill_sources,
                user_id=user_id,
                model_id=model_id,
                interrupt_on=interrupt_on,
                session_id=session_id,
            ),
            interrupt_on=interrupt_on,
            model_id=model_id,
        )

    async def run_agent(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        current_user=None,
        file_list: dict = None,
        qa_type: Optional[str] = None,
        model_id: Optional[str] = None,
        mcp_tools: Optional[list] = None,
        enabled_skills: Optional[list[str]] = None,
        db: Optional[AsyncSession] = None,
        kb_collections: Optional[list[str]] = None,
        kb_search_enabled: bool = True,
        disable_hitl: bool = False,
    ) -> AsyncGenerator[dict, None]:
        task_id = session_id or str(uuid.uuid4())
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        self.running_tasks[task_id] = {"cancelled": False}

        user_id = _resolve_user_id(current_user)
        if not session_id or not user_id:
            logger.warning(
                "SuperAgent 缺少 session_id 或 user_id，拒绝挂载可写 backend "
                f"session_id={session_id!r} user_id={user_id!r}"
            )
            yield {
                "type": "abort",
                "content": "",
                "tool_call": None,
                "reasoning": None,
                "finish_reason": "error",
                "usage": {},
            }
            return

        try:
            config = {"configurable": {"thread_id": task_id}, "recursion_limit": DEFAULT_RECURSION_LIMIT}

            async with agent_sandbox_session(user_id, session_id):
                agent = await self._create_compiled_agent(
                    user_id=user_id,
                    session_id=session_id,
                    model_id=model_id,
                    mcp_tools=mcp_tools,
                    enabled_skills=enabled_skills,
                    file_list=file_list,
                    db=db,
                    kb_collections=kb_collections,
                    kb_search_enabled=kb_search_enabled,
                    disable_hitl=disable_hitl,
                )

                human_kwargs = {}
                if session_id and user_id:
                    human_kwargs["noesis_attachments"] = {
                        "session_id": session_id,
                        "user_id": user_id,
                        "file_dict": file_list or {},
                    }

                human_message = HumanMessage(content=query, additional_kwargs=human_kwargs)
                if ChatAttachmentConfig.enabled and db is not None:
                    human_message = await AttachmentInputResolver(
                        session_id=session_id,
                        user_id=user_id,
                        db=db,
                        model_id=model_id,
                    ).resolve_human_message(query, additional_kwargs=human_kwargs)

                stream_args = {
                    "input": {
                        "messages": [human_message]
                    },
                    "config": config,
                    "stream_mode": "messages",
                    "langfuse_session_id": session_id,
                    "qa_type": qa_type,
                }

                async for chunk in self._stream_agent_response(
                    agent, stream_args, task_id, message_id
                ):
                    yield chunk

        except asyncio.CancelledError:
            logger.info(f"SuperAgent CancelledError task_id={task_id} session_id={session_id}")
            yield {
                "type": "abort",
                "content": "",
                "tool_call": None,
                "reasoning": None,
                "finish_reason": "stop",
                "usage": {},
            }
        except Exception as e:
            logger.exception(f"SuperAgent 运行异常: {e}")
            yield {
                "type": "abort",
                "content": "",
                "tool_call": None,
                "reasoning": None,
                "finish_reason": "error",
                "usage": {},
            }
        finally:
            if task_id in self.running_tasks:
                del self.running_tasks[task_id]

    async def resume_agent(
        self,
        *,
        session_id: str,
        decisions: list[dict],
        current_user=None,
        qa_type: Optional[str] = None,
        model_id: Optional[str] = None,
        mcp_tools: Optional[list] = None,
        enabled_skills: Optional[list[str]] = None,
        file_list: dict | None = None,
        db: Optional[AsyncSession] = None,
        message_id: Optional[str] = None,
        kb_collections: Optional[list[str]] = None,
        kb_search_enabled: bool = True,
        disable_hitl: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """从 HITL interrupt 以 ``Command(resume=...)`` 继续同一 thread。"""
        task_id = session_id
        mid = message_id or f"msg_{uuid.uuid4().hex[:16]}"
        self.running_tasks[task_id] = {"cancelled": False}
        user_id = _resolve_user_id(current_user)
        if not session_id or not user_id:
            yield {
                "type": "__tw_error__",
                "content": "缺少 session_id 或 user_id",
            }
            yield {"type": "__tw_finish__", "finish_reason": "error"}
            return

        try:
            config = {
                "configurable": {"thread_id": task_id},
                "recursion_limit": DEFAULT_RECURSION_LIMIT,
            }
            async with agent_sandbox_session(user_id, session_id):
                agent = await self._create_compiled_agent(
                    user_id=user_id,
                    session_id=session_id,
                    model_id=model_id,
                    mcp_tools=mcp_tools,
                    enabled_skills=enabled_skills,
                    file_list=file_list,
                    db=db,
                    kb_collections=kb_collections,
                    kb_search_enabled=kb_search_enabled,
                    disable_hitl=disable_hitl,
                )
                stream_args = {
                    "input": Command(resume={"decisions": decisions}),
                    "config": config,
                    "langfuse_session_id": session_id,
                    "qa_type": qa_type,
                }
                async for chunk in self._stream_agent_response(
                    agent, stream_args, task_id, mid
                ):
                    yield chunk
        except asyncio.CancelledError:
            logger.info(f"SuperAgent resume CancelledError session_id={session_id}")
            yield {"type": "__tw_abort__"}
            yield {"type": "__tw_finish__", "finish_reason": "stop"}
        except Exception as e:
            logger.exception(f"SuperAgent resume 异常: {e}")
            yield {"type": "__tw_error__", "content": str(e)}
            yield {"type": "__tw_finish__", "finish_reason": "error"}
        finally:
            if task_id in self.running_tasks:
                del self.running_tasks[task_id]
