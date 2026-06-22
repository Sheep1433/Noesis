"""简单 MCP 集成 Agent - SimpleMCPAgent（调试用）

基于 create_noesis_agent；用于本地调试 MCP 连接，无数据库依赖。
"""

import asyncio
import uuid
from typing import AsyncGenerator, Optional

from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.base.base_agent import BaseAgent, DEFAULT_RECURSION_LIMIT
from agent.factory import create_noesis_agent
from agent.prompts import PromptProfile, build_prompt
from agent.tools.mcp_invoke_wrapper import wrap_mcp_tools
from common.logging import logger


class SimpleMCPAgent(BaseAgent):
    """简单的 MCP 集成 Agent"""

    def __init__(self, mcp_url: str = "http://localhost:8000/mcp"):
        super().__init__()
        self.mcp_url = mcp_url

    async def run_agent(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        file_list: dict = None,
    ) -> AsyncGenerator[dict, None]:
        """运行 Agent 并返回流式响应"""
        task_id = session_id or str(uuid.uuid4())
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        self.running_tasks[task_id] = {"cancelled": False}

        try:
            # 连接 MCP 服务器
            mcp_client = MultiServerMCPClient({
                "ssh": {
                    "url": self.mcp_url,
                    "transport": "streamable_http",
                }
            })

            try:
                all_tools = wrap_mcp_tools(await mcp_client.get_tools())
                logger.info(f"获取到 {len(all_tools)} 个 MCP 工具")
            except Exception as e:
                logger.error(f"获取 MCP 工具失败: {e}")
                yield {
                    "type": "abort",
                    "content": f"无法连接 MCP 服务: {e}",
                    "tool_call": None,
                    "reasoning": None,
                    "finish_reason": "error",
                    "usage": {}
                }
                return

            config = {
                "configurable": {"thread_id": task_id},
                "recursion_limit": DEFAULT_RECURSION_LIMIT
            }

            agent = create_noesis_agent(
                tools=all_tools,
                system_prompt=build_prompt(PromptProfile.SIMPLE_MCP),
                checkpointer=self.checkpointer,
            )

            # 流式执行
            stream_args = {
                "input": {"messages": [HumanMessage(content=query)]},
                "config": config,
                "stream_mode": "messages",
            }

            async for chunk in self._stream_agent_response(
                agent, stream_args, task_id, message_id
            ):
                yield chunk

        except asyncio.CancelledError:
            logger.info(
                f"SimpleMCPAgent CancelledError task_id={task_id} session_id={session_id}"
            )
            yield {"type": "abort", "content": "", "tool_call": None, "reasoning": None, "finish_reason": "stop", "usage": {}}
        except Exception as e:
            logger.exception(f"SimpleMCPAgent error: {e}")
            yield {"type": "abort", "content": "", "tool_call": None, "reasoning": None, "finish_reason": "error", "usage": {}}
        finally:
            if task_id in self.running_tasks:
                del self.running_tasks[task_id]


# 独立运行测试
if __name__ == "__main__":
    import asyncio

    async def test():
        agent = SimpleMCPAgent(mcp_url="http://localhost:8000/mcp")
        async for chunk in agent.run_agent("你好，列出可用的工具"):
            print(chunk)

    asyncio.run(test())
