"""
DeepResearchAgent - 深度调研智能体

基于 create_noesis_agent + 沙箱 backend（AIO 单盘或 local_shell CompositeBackend）。
"""

import asyncio
import uuid
from typing import AsyncGenerator, Optional

from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import HumanMessage

from agent.base.base_agent import BaseAgent, DEFAULT_RECURSION_LIMIT
from agent.factory import build_subagent_default_middleware, create_noesis_agent
from agent.prompts import PromptProfile, build_prompt
from agent.tools import build_web_search_tools
from agent.backends import SKILL_SOURCES, agent_sandbox_session, create_agent_backend
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.skills import SkillsMiddleware
from deepagents.middleware.subagents import SubAgent
from llm import get_llm
from common.logging import logger


def _resolve_user_id(current_user) -> Optional[str]:
    if current_user is None:
        return None
    uid = getattr(current_user, "user_id", None)
    return str(uid) if uid is not None else None


def _build_deep_research_subagents(
    backend: BackendProtocol,
    web_tools: list,
) -> list[SubAgent]:
    """深度研究子 Agent：独立上下文内执行单课题调研（filesystem + skills + web）。"""
    subagent_middleware = [
        *build_subagent_default_middleware(backend),
        SkillsMiddleware(backend=backend, sources=list(SKILL_SOURCES)),
    ]
    return [
        {
            "name": "research-worker",
            "description": (
                "在独立上下文中完成单课题深度调研：阅读 `/workspace/skills/` 相关 skill（含 deep-research-v2）、"
                "使用 web_search/web_fetch 检索互联网、在工作区读写与归纳文件，多步后返回结构化小结。"
                "适合可并行、上下文较重的子任务。"
            ),
            "system_prompt": build_prompt(PromptProfile.DEEP_RESEARCH_SUB),
            "model": get_llm(),
            "tools": web_tools,
            "middleware": subagent_middleware,
            "skills": list(SKILL_SOURCES),
        },
    ]


class DeepResearchAgent(BaseAgent):
    """深度调研智能体。"""

    def __init__(self):
        super().__init__()

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
                "DeepResearchAgent 缺少 session_id 或 user_id，拒绝挂载可写 backend "
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
                backend = await create_agent_backend(user_id, session_id)
                web_tools = build_web_search_tools()
                agent = create_noesis_agent(
                    tools=web_tools,
                    system_prompt=build_prompt(PromptProfile.DEEP_RESEARCH),
                    checkpointer=self.checkpointer,
                    backend=backend,
                    subagents=_build_deep_research_subagents(backend, web_tools),
                    extra_middleware=[
                        TodoListMiddleware(),
                        SkillsMiddleware(backend=backend, sources=list(SKILL_SOURCES)),
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
            logger.info(
                f"DeepResearchAgent CancelledError task_id={task_id} session_id={session_id}"
            )
            yield {"type": "abort", "content": "", "tool_call": None, "reasoning": None, "finish_reason": "stop", "usage": {}}
        except Exception as e:
            logger.exception(f"DeepResearchAgent 运行异常: {e}")
            yield {"type": "abort", "content": "", "tool_call": None, "reasoning": None, "finish_reason": "error", "usage": {}}
        finally:
            if task_id in self.running_tasks:
                del self.running_tasks[task_id]
