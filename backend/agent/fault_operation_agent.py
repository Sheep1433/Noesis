"""故障运维智能体 - FaultOperationAgent

基于 create_noesis_agent + MCP 运维工具 + SubAgentMiddleware（general-purpose 子 Agent）。
MCP 连接见 extensions/mcp/mcp.json（profile: fault_operation）。
"""

import asyncio
import uuid
from typing import Any, AsyncGenerator, Optional

from langchain_core.messages import HumanMessage

from agent.base.base_agent import BaseAgent, DEFAULT_RECURSION_LIMIT
from agent.factory import build_subagent_default_middleware, create_noesis_agent
from agent.mcp.loader import load_mcp_tools
from agent.prompts import PromptProfile, build_prompt
from agent.backends import agent_sandbox_session, create_agent_backend
from config.mcp_config import MCP_PROFILE_FAULT_OPERATION
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.subagents import SubAgent
from llm import get_llm
from common.logging import logger


def _resolve_user_id(current_user) -> Optional[str]:
    if current_user is None:
        return None
    uid = getattr(current_user, "user_id", None)
    return str(uid) if uid is not None else None


def _build_fault_operation_subagents(
    backend: BackendProtocol,
    mcp_tools: list[Any],
    *,
    model_id: str | None = None,
) -> list[SubAgent]:
    """与 deepagents 默认 general-purpose 对齐：独立上下文内执行多步 MCP 运维子任务。"""
    return [
        {
            "name": "general-purpose",
            "description": (
                "通用运维子 Agent：在独立上下文中完成多步远程诊断（日志检索、命令执行、"
                "配置读取等），具备与主 Agent 相同的 MCP 工具。适合可并行、上下文较重的排查子任务。"
            ),
            "system_prompt": build_prompt(PromptProfile.FAULT_OPERATION_SUB),
            "model": get_llm(model_id=model_id),
            "tools": mcp_tools,
            "middleware": build_subagent_default_middleware(backend),
        },
    ]


class FaultOperationAgent(BaseAgent):
    """故障运维智能体 - MCP 工具 + create_noesis_agent"""

    def __init__(self, mcp_profile: str = MCP_PROFILE_FAULT_OPERATION):
        super().__init__()
        self.mcp_profile = mcp_profile

    async def _load_mcp_tools(self) -> list:
        return await load_mcp_tools(self.mcp_profile)

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
    ) -> AsyncGenerator[dict, None]:
        """运行 Agent 并返回流式响应"""
        task_id = session_id or str(uuid.uuid4())
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        self.running_tasks[task_id] = {"cancelled": False}

        user_id = _resolve_user_id(current_user)
        if not session_id or not user_id:
            logger.warning(
                "FaultOperationAgent 缺少 session_id 或 user_id，拒绝挂载可写 backend "
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
            config = {
                "configurable": {"thread_id": task_id},
                "recursion_limit": DEFAULT_RECURSION_LIMIT,
            }

            async with agent_sandbox_session(user_id, session_id):
                resolved_mcp = (
                    list(mcp_tools)
                    if mcp_tools is not None
                    else await self._load_mcp_tools()
                )
                backend = await create_agent_backend(user_id, session_id)

                agent = create_noesis_agent(
                    tools=resolved_mcp,
                    system_prompt=build_prompt(PromptProfile.FAULT_OPERATION),
                    checkpointer=self.checkpointer,
                    backend=backend,
                    subagents=_build_fault_operation_subagents(
                        backend, resolved_mcp, model_id=model_id
                    ),
                    model_id=model_id,
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
                f"FaultOperationAgent CancelledError task_id={task_id} session_id={session_id}"
            )
            yield {"type": "abort", "content": "", "tool_call": None, "reasoning": None, "finish_reason": "stop", "usage": {}}
        except Exception as e:
            logger.exception(f"FaultAgent error: {e}")
            yield {"type": "abort", "content": "", "tool_call": None, "reasoning": None, "finish_reason": "error", "usage": {}}
        finally:
            if task_id in self.running_tasks:
                del self.running_tasks[task_id]
