"""
通用智能问答 Agent - GeneralQAAgent

基于 create_noesis_agent；向量库可用时挂载 search_knowledge_base（跨全部 Collection hybrid 检索）。
"""
import asyncio
import uuid
from typing import AsyncGenerator, Optional

from langchain_core.messages import HumanMessage

from agent.base.base_agent import BaseAgent, DEFAULT_RECURSION_LIMIT
from agent.factory import create_noesis_agent
from agent.prompts import PromptProfile, build_prompt
from agent.tools import build_kb_search_tools, build_web_search_tools, list_qdrant_collection_names
from utils.log_util import logger


class GeneralQAAgent(BaseAgent):
    """通用智能问答 Agent"""

    def __init__(self):
        super().__init__()

    async def run_agent(
        self,
        query: str,
        session_id: Optional[str] = None,
        conversation_id: str = None,
        current_user=None,
        file_list: dict = None,
        qa_type: Optional[str] = None,
        kb_collections: Optional[list] = None,
    ) -> AsyncGenerator[dict, None]:
        task_id = conversation_id if conversation_id else str(uuid.uuid4())
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        task_context = {"cancelled": False}
        self.running_tasks[task_id] = task_context

        kb_tools = build_kb_search_tools()
        web_tools = build_web_search_tools()
        tools = kb_tools + web_tools
        kb_enabled = len(kb_tools) > 0
        if kb_enabled:
            logger.info(
                f"GeneralQAAgent kb_search collections={list_qdrant_collection_names()}"
            )

        try:
            thread_id = session_id if session_id else "default_thread"
            config = {"configurable": {"thread_id": thread_id}, "recursion_limit": DEFAULT_RECURSION_LIMIT}

            agent = create_noesis_agent(
                tools=tools,
                system_prompt=build_prompt(PromptProfile.COMMON_QA, kb_enabled=kb_enabled),
                checkpointer=self.checkpointer,
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
                f"GeneralQAAgent CancelledError task_id={task_id} session_id={session_id}"
            )
            yield {"type": "abort", "content": "", "tool_call": None, "reasoning": None, "finish_reason": "stop", "usage": {}}
        except Exception as e:
            logger.exception(f"Agent运行异常: {e}")
            yield {"type": "abort", "content": "", "tool_call": None, "reasoning": None, "finish_reason": "error", "usage": {}}
        finally:
            if task_id in self.running_tasks:
                del self.running_tasks[task_id]
