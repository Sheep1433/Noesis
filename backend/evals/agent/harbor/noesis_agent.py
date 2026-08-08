"""Harbor BaseAgent adapter backed directly by the Noesis Harness."""

from __future__ import annotations

import asyncio
import os
import uuid
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import HumanMessage

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from evals.agent.harbor.harbor_backend import HarborBackend
from evals.bootstrap import eval_runtime
from noesis.config import ModelConfig
from noesis.factory import create_noesis_agent
from noesis.llm import build_chat_model, get_llm
from noesis.agents.prompts.execution import build_execution_sections
from noesis.runtime import DEFAULT_RECURSION_LIMIT, stream_agent_events

AGENT_VERSION = "0.1.0"


def _build_system_prompt(*, working_dir: str) -> str:
    sections = [
        "<role>",
        "你是终端任务智能体，在隔离的 Linux 容器内完成用户指令。",
        "使用 ls、read_file、write_file、edit_file、execute、grep、glob 等工具真实操作容器文件系统。",
        "</role>",
        "<environment>",
        f"- 容器工作目录（execute 默认 cwd）：`{working_dir}`",
        "- 文件路径须为绝对路径；先用 ls 熟悉目录结构。",
        "- 交付前用命令或 read_file 验证结果。",
        "</environment>",
        *build_execution_sections(include_tool_enforcement=True),
    ]
    return "\n\n".join(sections)


def _resolve_llm(model_name: str | None):
    normalized = (model_name or "").strip()
    if normalized.startswith("opencode/"):
        return build_chat_model(
            model_type="opencode",
            model_name=normalized.split("/", maxsplit=1)[1],
            temperature=float(os.getenv("HARBOR_NOESIS_TEMPERATURE", "0") or 0),
            model_base_url=os.getenv("OPENCODE_API_BASE", "").strip()
            or ModelConfig.model_base_url,
            model_api_key=os.getenv("OPENCODE_API_KEY", "public"),
        )
    if normalized:
        from noesis.llm.catalog import get_model_catalog

        if normalized in {entry.id for entry in get_model_catalog()}:
            return get_llm(model_id=normalized)
    return get_llm()


class NoesisHarborAgent(BaseAgent):
    """Run the Noesis Harness through Harbor's official custom-agent API."""

    @staticmethod
    def name() -> str:
        return "noesis-harbor"

    def version(self) -> str | None:
        return AGENT_VERSION

    async def setup(self, environment: BaseEnvironment) -> None:
        return

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del context
        session_id = environment.session_id or str(uuid.uuid4())
        pwd = await environment.exec("pwd")
        working_dir = (pwd.stdout or "/").strip()
        backend = HarborBackend(
            environment,
            loop=asyncio.get_running_loop(),
            cwd=working_dir,
        )

        async with eval_runtime() as checkpointer:
            agent = create_noesis_agent(
                tools=[],
                system_prompt=_build_system_prompt(working_dir=working_dir),
                checkpointer=checkpointer,
                backend=backend,
                extra_middleware=[TodoListMiddleware()],
                model=_resolve_llm(self.model_name),
            )
            async for _ in stream_agent_events(
                agent,
                {
                    "input": {"messages": [HumanMessage(content=instruction)]},
                    "config": {
                        "configurable": {"thread_id": session_id},
                        "recursion_limit": DEFAULT_RECURSION_LIMIT,
                    },
                    "langfuse_session_id": session_id,
                    "qa_type": "HARBOR_EVAL",
                },
                task_id=session_id,
                message_id=f"msg_{uuid.uuid4().hex[:16]}",
            ):
                pass
