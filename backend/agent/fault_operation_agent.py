"""故障运维智能体 - FaultOperationAgent

基于 create_noesis_agent + MCP 运维工具 + SubAgentMiddleware（general-purpose 子 Agent）。
"""

import asyncio
import os
import uuid
from typing import Any, AsyncGenerator, Optional

from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.base.base_agent import BaseAgent, DEFAULT_RECURSION_LIMIT
from agent.factory import build_subagent_default_middleware, create_noesis_agent
from agent.prompts import PromptProfile, build_prompt
from agent.backends import create_local_shell_backend
from deepagents.backends import LocalShellBackend
from deepagents.middleware.subagents import SubAgent
from llm import get_llm
from utils.log_util import logger

# 故障运维 MCP 端点（与 SimpleMCPAgent 调试地址一致，按需改代码）
FAULT_MCP_URL = "http://localhost:8000/mcp"

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FAULT_WORKSPACE_DIR = os.path.join(_BACKEND_DIR, ".agent_workspace", "fault_ops")


def _build_fault_backend() -> LocalShellBackend:
    """本地工作区：存放排查笔记、临时脚本等；并满足 SubAgentMiddleware 对 backend 的要求。"""
    os.makedirs(_FAULT_WORKSPACE_DIR, exist_ok=True)
    return create_local_shell_backend(_FAULT_WORKSPACE_DIR, virtual_mode=True)


def _build_fault_operation_subagents(
    backend: LocalShellBackend,
    mcp_tools: list[Any],
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
            "model": get_llm(),
            "tools": mcp_tools,
            "middleware": build_subagent_default_middleware(backend),
        },
    ]


class FaultOperationAgent(BaseAgent):
    """故障运维智能体 - MCP 工具 + create_noesis_agent"""

    def __init__(self, mcp_url: str = FAULT_MCP_URL):
        super().__init__()
        self.mcp_url = mcp_url

    async def _load_mcp_tools(self) -> list:
        mcp_client = MultiServerMCPClient(
            {
                "fault_ops": {
                    "url": self.mcp_url,
                    "transport": "streamable_http",
                }
            }
        )
        tools = await mcp_client.get_tools()
        logger.info(f"FaultOperationAgent 加载 MCP 工具 {len(tools)} 个")
        return tools

    async def run_agent(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        file_list: dict = None,
        qa_type: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        """运行 Agent 并返回流式响应"""
        task_id = session_id or str(uuid.uuid4())
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        self.running_tasks[task_id] = {"cancelled": False}

        try:
            config = {
                "configurable": {"thread_id": task_id},
                "recursion_limit": DEFAULT_RECURSION_LIMIT,
            }

            mcp_tools = await self._load_mcp_tools()
            backend = _build_fault_backend()

            agent = create_noesis_agent(
                tools=mcp_tools,
                system_prompt=build_prompt(PromptProfile.FAULT_OPERATION),
                checkpointer=self.checkpointer,
                backend=backend,
                subagents=_build_fault_operation_subagents(backend, mcp_tools),
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
