"""SuperAgent - 通用超级智能体（filesystem + skills + web + 用户记忆）。"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncGenerator, Optional

from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.subagents import SubAgent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import HumanMessage

from agent.backends import SKILL_SOURCES, agent_sandbox_session, create_agent_backend
from agent.backends.mount_paths import AGENT_MEMORY_AGENTS_FILE, AGENT_MEMORY_USER_FILE
from agent.base.base_agent import BaseAgent, DEFAULT_RECURSION_LIMIT
from agent.factory import build_subagent_default_middleware, create_noesis_agent
from agent.middlewares.memory_prompt import NOESIS_MEMORY_SYSTEM_PROMPT
from agent.middlewares.memory_sync_middleware import MemorySyncMiddleware
from agent.prompts import PromptProfile, build_prompt
from agent.prompts.super_agent import NOESIS_SKILLS_SYSTEM_PROMPT
from agent.tools import build_web_search_tools
from common.logging import logger
from config.user_data_paths import ensure_user_memory_files
from llm import get_llm

_MEMORY_SOURCES = [AGENT_MEMORY_USER_FILE, AGENT_MEMORY_AGENTS_FILE]


def _resolve_user_id(current_user) -> Optional[str]:
    if current_user is None:
        return None
    uid = getattr(current_user, "user_id", None)
    return str(uid) if uid is not None else None


def _build_memory_middleware(backend: BackendProtocol) -> list:
    return [
        MemoryMiddleware(
            backend=backend,
            sources=_MEMORY_SOURCES,
            system_prompt=NOESIS_MEMORY_SYSTEM_PROMPT,
        ),
        MemorySyncMiddleware(backend=backend, sources=_MEMORY_SOURCES),
    ]


def _build_task_worker_subagents(
    backend: BackendProtocol,
    web_tools: list,
) -> list[SubAgent]:
    subagent_middleware = [
        *build_subagent_default_middleware(backend),
        SkillsMiddleware(
            backend=backend,
            sources=list(SKILL_SOURCES),
            system_prompt=NOESIS_SKILLS_SYSTEM_PROMPT,
        ),
    ]
    return [
        {
            "name": "task-worker",
            "description": (
                "在独立上下文中完成主 Agent 委派的单个子任务：阅读 `/skills/extensions/` 与 `/skills/custom/` 相关 Skill、"
                "使用 web_search/web_fetch 检索、在工作区读写与归纳文件，多步后返回结构化小结。"
                "适合可并行、上下文较重的子任务（调研子课题、多源检索、批量读文件等）。"
            ),
            "system_prompt": build_prompt(PromptProfile.SUPER_AGENT_SUB),
            "model": get_llm(),
            "tools": web_tools,
            "middleware": subagent_middleware,
            "skills": list(SKILL_SOURCES),
        },
    ]


class SuperAgent(BaseAgent):
    """通用超级智能体。"""

    async def run_agent(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        current_user=None,
        file_list: dict = None,
        qa_type: Optional[str] = None,
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
                ensure_user_memory_files(user_id)
                backend = await create_agent_backend(user_id, session_id)
                web_tools = build_web_search_tools()
                agent = create_noesis_agent(
                    tools=web_tools,
                    system_prompt=build_prompt(PromptProfile.SUPER_AGENT),
                    checkpointer=self.checkpointer,
                    backend=backend,
                    subagents=_build_task_worker_subagents(backend, web_tools),
                    extra_middleware=[
                        TodoListMiddleware(),
                        SkillsMiddleware(
                            backend=backend,
                            sources=list(SKILL_SOURCES),
                            system_prompt=NOESIS_SKILLS_SYSTEM_PROMPT,
                        ),
                        *_build_memory_middleware(backend),
                    ],
                )

                stream_args = {
                    "input": {"messages": [HumanMessage(content=query)]},
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
