from collections.abc import AsyncGenerator

from noesis.runtime.logging import logger
from noesis.config.checkpointer import get_checkpointer
from noesis.runtime.stream import DEFAULT_RECURSION_LIMIT, stream_agent_events

__all__ = ["BaseAgent", "DEFAULT_RECURSION_LIMIT"]


class BaseAgent:
    """
    Agent 基类，定义通用接口和方法
    使用 LangChain astream_events 流式输出；直接产出 LangGraph 事件 dict（见 langgraph_bridge_bridge）。
    """

    def __init__(self):
        self.running_tasks = {}

    @property
    def checkpointer(self):
        return get_checkpointer()

    async def cancel_task(self, task_id: str) -> bool:
        """取消指定的任务"""
        if task_id in self.running_tasks:
            self.running_tasks[task_id]["cancelled"] = True
            logger.info(f"BaseAgent.cancel_task 已标记取消 task_id={task_id}")
            return True
        logger.info(f"BaseAgent.cancel_task 无运行中任务 task_id={task_id}")
        return False

    def get_running_tasks(self):
        """获取当前运行中的任务列表"""
        return list(self.running_tasks.keys())

    async def _stream_agent_response(
        self, agent, stream_args, task_id: str, _message_id: str
    ) -> AsyncGenerator[dict, None]:
        """委托 ``noesis.runtime.stream.stream_agent_events``（评测可直接调用后者）。"""
        async for event in stream_agent_events(
            agent,
            stream_args,
            task_id=task_id,
            message_id=_message_id,
            is_cancelled=lambda: bool(
                self.running_tasks.get(task_id, {}).get("cancelled")
            ),
        ):
            yield event
