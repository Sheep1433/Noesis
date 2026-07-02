"""
通用智能问答 Agent - GeneralQAAgent

基于 create_noesis_agent；向量库可用时挂载知识库 Tool（可选范围 hybrid 检索）。
"""
import asyncio
import uuid
from typing import AsyncGenerator, List, Optional

from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from agent.base.base_agent import BaseAgent, DEFAULT_RECURSION_LIMIT
from agent.factory import create_noesis_agent
from agent.middlewares.chat_attachments_middleware import ChatAttachmentsMiddleware
from agent.prompts import PromptProfile, build_prompt
from agent.tools import build_kb_search_tools, build_web_search_tools, list_qdrant_collection_names
from agent.tools.chat_attachment_tools import build_attachment_tools
from config.env import ChatAttachmentConfig
from services.chat_attachment_service import ChatAttachmentService
from common.logging import logger
from domain.chat.attachments.vision import is_vision_available


def _normalize_kb_collections(raw: Optional[List[str]]) -> List[str]:
    if not raw:
        return []
    seen: set[str] = set()
    out: List[str] = []
    for item in raw:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


class GeneralQAAgent(BaseAgent):
    """通用智能问答 Agent"""

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
        kb_collections: Optional[list] = None,
        db: Optional[AsyncSession] = None,
    ) -> AsyncGenerator[dict, None]:
        task_id = session_id or str(uuid.uuid4())
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        self.running_tasks[task_id] = {"cancelled": False}

        scoped_collections = _normalize_kb_collections(kb_collections)
        kb_tools = build_kb_search_tools(
            default_collection_names=scoped_collections or None,
        )
        web_tools = build_web_search_tools()
        tools = kb_tools + web_tools
        kb_enabled = len(kb_tools) > 0
        if kb_enabled:
            scope_label = scoped_collections or list_qdrant_collection_names()
            logger.info(
                f"GeneralQAAgent kb_tools={len(kb_tools)} scope={scope_label}"
            )

        user_id = str(getattr(current_user, "user_id", "") or "")
        attachments_enabled = False
        extra_middleware = None

        if (
            ChatAttachmentConfig.enabled
            and db is not None
            and session_id
            and user_id
        ):
            attachments_enabled = await ChatAttachmentService.session_has_attachments(
                session_id=session_id,
                user_id=user_id,
                db=db,
                file_dict=file_list,
            )
            if attachments_enabled:
                tools = tools + build_attachment_tools(
                    session_id=session_id,
                    user_id=user_id,
                    db=db,
                )
                extra_middleware = [
                    ChatAttachmentsMiddleware(
                        session_id=session_id,
                        user_id=user_id,
                        db=db,
                        vision_available=is_vision_available(),
                    )
                ]

        try:
            config = {"configurable": {"thread_id": task_id}, "recursion_limit": DEFAULT_RECURSION_LIMIT}

            agent = create_noesis_agent(
                tools=tools,
                system_prompt=build_prompt(
                    PromptProfile.COMMON_QA,
                    kb_enabled=kb_enabled,
                    attachments_enabled=attachments_enabled,
                    kb_scope_collections=scoped_collections or None,
                ),
                checkpointer=self.checkpointer,
                extra_middleware=extra_middleware,
            )

            human_kwargs = {}
            if session_id and user_id:
                human_kwargs["noesis_attachments"] = {
                    "session_id": session_id,
                    "user_id": user_id,
                    "file_dict": file_list or {},
                }

            stream_args = {
                "input": {
                    "messages": [
                        HumanMessage(content=query, additional_kwargs=human_kwargs)
                    ]
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
