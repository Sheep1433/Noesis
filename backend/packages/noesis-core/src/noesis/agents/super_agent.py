"""SuperAgent - 通用超级智能体（filesystem + skills + web + 用户记忆）。"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncGenerator, Optional

from deepagents.backends.protocol import BackendProtocol
from langgraph.types import Command
from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.agents.backends import agent_sandbox_session, create_agent_backend
from noesis.agents.backends.paths import AGENT_MEMORY_AGENTS_FILE, AGENT_MEMORY_INDEX_FILE, AGENT_MEMORY_USER_FILE
from noesis.agents.base import BaseAgent, DEFAULT_RECURSION_LIMIT
from noesis.factory import build_noesis_middleware, create_noesis_agent
from noesis.agents.tools.ask_user import ask_user_tool, build_interrupt_on
from noesis.agents.prompts import PromptProfile, build_prompt
from noesis.agents.prompts.memory import NOESIS_MEMORY_SYSTEM_PROMPT
from noesis.agents.prompts.super_agent import NOESIS_SKILLS_SYSTEM_PROMPT
from noesis.agents.skills import resolve_skill_sources_for_session
from noesis.agents.subagents import (
    BackgroundSubagentExecutor,
    BgNotifyMiddleware,
    build_background_task_tools,
)
from noesis.agents.subagents.shell_tool import replace_execute_tool
from noesis.config.env import HitlConfig, SubagentConfig
from noesis.agents.tools import build_web_search_tools
from noesis.agents.tools.chat_attachment_tools import build_attachment_tools
from noesis.agents.tools.kb_search_tool import build_kb_search_tools
from noesis.agents.tools.memory_tools import build_memory_tools
from noesis.runtime.logging import logger
from noesis.config.env import ChatAttachmentConfig
from noesis.config.user_data_paths import ensure_user_memory_files
from noesis.agents.context import ContextResolver
from noesis.llm.factory import get_llm
from noesis.runtime.deps import require_attachment_service
from noesis.runtime.attachments.input_resolver import AttachmentInputResolver
from noesis.services.chat_service import ChatService

_MEMORY_SOURCES = [AGENT_MEMORY_USER_FILE, AGENT_MEMORY_AGENTS_FILE, AGENT_MEMORY_INDEX_FILE]


def _resolve_user_id(current_user) -> Optional[str]:
    if current_user is None:
        return None
    uid = getattr(current_user, "user_id", None)
    return str(uid) if uid is not None else None


def _compile_task_worker(
    backend: BackendProtocol,
    tools: list,
    skill_sources: list,
    *,
    user_id: str,
    model_id: str | None = None,
    interrupt_on: dict | None = None,
    session_id: str = "",
    checkpointer=None,
):
    """编译后台 task-worker：独立上下文 + 自带 HITL interrupt，供 BackgroundSubagentExecutor 使用。"""
    from langchain.agents import create_agent
    from langchain.agents.middleware import HumanInTheLoopMiddleware

    model = get_llm(model_id=model_id)
    middleware = list(build_noesis_middleware(
        profile="SUBAGENT",
        model=model,
        model_id=model_id,
        tools=tools,
        backend=backend,
        skills=skill_sources,
        skills_user_id=user_id,
        skills_system_prompt=NOESIS_SKILLS_SYSTEM_PROMPT,
        session_id=session_id,
    ))
    if interrupt_on:
        # 后台任务审批：interrupt 落 checkpoint，executor 转 awaiting_approval，
        # 审批 API 用 Command(resume) 在同一 thread 续跑
        middleware.append(HumanInTheLoopMiddleware(interrupt_on=interrupt_on))
    return create_agent(
        model,
        system_prompt=build_prompt(PromptProfile.SUPER_AGENT_SUB),
        tools=tools,
        middleware=middleware,
        name="task-worker",
        checkpointer=checkpointer,
    )


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
        run_id: Optional[str] = None,
    ):
        ensure_user_memory_files(user_id)
        backend = await create_agent_backend(user_id, session_id)
        web_tools = build_web_search_tools()
        tools = list(web_tools) + list(mcp_tools or [])
        # Agentic 召回：root run 装配检索工具（命中后合并回写 run.memory_context，
        # 作为抽取防自强化输入）；run_id/db 缺席时退化为纯只读检索
        tools.extend(build_memory_tools(user_id=user_id, run_id=run_id, db=db))
        # KB 检索工具（用户勾选启用时挂载）
        if kb_search_enabled and kb_collections is not None:
            kb_tools = build_kb_search_tools(
                default_collection_names=kb_collections,
                enforce_scope=bool(kb_collections),
            )
            if kb_tools:
                tools.extend(kb_tools)
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

        # 后台子 Agent（全异步 task）：主 Agent 用 start/check 工具委派，
        # 子任务在进程内隔离 loop 跑，生命周期归属 session，跨 run 可收结果。
        # worker 不携带后台任务工具自身（避免递归委派）。
        # worker 经工厂在隔离 loop 内惰性编译：LLM 客户端与 checkpointer
        # 连接池必须绑定隔离 loop（复用主 loop 实例会 cross-loop 报错）。
        # worker 的检索只读不写：召回清单只归 root run（防自强化输入），
        # 子 Agent 结论经父会话终态回流
        worker_tools = [
            tool for tool in tools if getattr(tool, "name", "") != "search_memory"
        ] + build_memory_tools(user_id=user_id)

        async def _bg_worker_factory(model_id_override: str | None = None):
            from noesis.config.checkpointer import create_isolated_checkpointer

            return _compile_task_worker(
                # worker 专用 backend：/memory 只读（沙箱按 user+session 幂等
                # 复用，二次组装不产生新容器）；记忆更新由主 Agent 收小结后
                # 自行完成，避免「委派写记忆 → 连环审批 → 拒后重试」
                await create_agent_backend(user_id, session_id, memory_read_only=True),
                worker_tools,
                skill_sources,
                user_id=user_id,
                # followup 可按 turn 切换模型：覆盖优先，否则沿用父 Agent 模型
                model_id=model_id_override or model_id,
                interrupt_on=(
                    build_interrupt_on(session_id=session_id, memory_write_guard=False)
                    if interrupt_on is not None else None
                ),
                session_id=session_id,
                checkpointer=await create_isolated_checkpointer(),
            )

        bg_executor = BackgroundSubagentExecutor(
            max_concurrent_per_session=SubagentConfig.max_concurrent_per_session,
            task_timeout_seconds=SubagentConfig.task_timeout_seconds,
            shell_task_timeout_seconds=SubagentConfig.shell_task_timeout_seconds,
            hitl_timeout_seconds=HitlConfig.ask_timeout_seconds,
            stop_grace_seconds=SubagentConfig.stop_grace_seconds,
        )

        async def _create_child_session(
            description: str,
            prompt: str | None = None,
            tool_call_id: str = "",
        ) -> dict[str, str]:
            # 工具可能在并行 tool-call 中同时创建多个子 Agent；不要复用请求级
            # AsyncSession，单独取连接保证每个 launch 有独立事务边界。
            from noesis.storage.postgres.manager import pg_manager

            async with pg_manager.get_async_session_context() as child_db:
                from noesis.services.subagent_session_service import SubagentSessionService

                # The launch use case owns the child session, initial messages and
                # standard AgentRun in one transaction.  Keep this callback small so
                # the tool layer cannot accidentally create a second source of truth.
                # description = 简短标题（会话标题）；prompt = 完整任务指令（首条用户消息）
                launch = await SubagentSessionService.launch(
                    parent_session_id=session_id,
                    user_id=user_id,
                    description=description,
                    prompt=prompt,
                    tool_call_id=tool_call_id or None,
                    model_id=model_id,
                    db=child_db,
                )
                return launch.to_dict()

        async def _delete_child_session(child_session_id: str) -> None:
            from noesis.storage.postgres.manager import pg_manager

            async with pg_manager.get_async_session_context() as child_db:
                await ChatService.delete_session(child_session_id, user_id, db=child_db)

        async def _fail_child_run(run_id: str, error: str) -> None:
            from noesis.services.subagent_session_service import SubagentSessionService

            await SubagentSessionService.mark_launch_rejected(run_id, error)

        async def _create_followup_run(
            child_session_id: str,
            message: str,
            user_message_id: str | None = None,
        ) -> dict[str, str]:
            """冷恢复 / 链式 followup 的新 run 创建。

            经 run_on_main_loop 在主 loop 执行：pg_manager 连接池绑定主
            loop，而本工厂在 executor 隔离 loop 上被调用（send_message 冷
            恢复与运行中 followup 链两处）——直连会触发 asyncpg 跨 loop
            连接错误，冷恢复曾因此静默失败（任务卡 RUNNING、追问无回复）。
            """
            from noesis.runtime.main_loop import run_on_main_loop
            from noesis.services.subagent_session_service import SubagentSessionService
            from noesis.storage.postgres.manager import pg_manager

            async def _launch() -> dict[str, str]:
                async with pg_manager.get_async_session_context() as child_db:
                    launch = await SubagentSessionService.create_followup_run(
                        session_id=child_session_id,
                        user_id=user_id,
                        message=message,
                        user_message_id=user_message_id,
                        db=child_db,
                    )
                    return launch.to_dict()

            future = run_on_main_loop(
                _launch(), name=f"subagent-followup-launch:{child_session_id}",
            )
            if future is None:
                raise RuntimeError("主 loop 不可用，followup run 创建失败")
            return await asyncio.wrap_future(future)

        tools.extend(build_background_task_tools(
            worker_factory=_bg_worker_factory,
            executor=bg_executor,
            session_id=session_id,
            user_id=user_id,
            create_child_session=_create_child_session,
            delete_child_session=_delete_child_session,
            fail_child_run=_fail_child_run,
            create_followup_run=_create_followup_run,
            model_id=model_id,
        ))

        return create_noesis_agent(
            profile="SUPER_AGENT_QA",
            tools=tools,
            system_prompt=resolved_context.system_prompt,
            checkpointer=self.checkpointer,
            # run 内即时感知后台任务终态：下一次模型调用注入 [系统通知]
            middleware=[BgNotifyMiddleware(session_id=session_id)],
            backend=backend,
            # execute 工具后台化（run_in_background，默认 false 前台零变化）；
            # 仅主 Agent 挂载——task-worker 保持前台 execute（禁止递归后台化）
            filesystem_middleware_hook=lambda fm: replace_execute_tool(
                fm,
                executor=bg_executor,
                backend=backend,
                session_id=session_id,
                user_id=user_id,
            ),
            workspace="/workspace",
            session_id=session_id,
            attachments=tuple(str(name) for name in (file_list or {})),
            skills=skill_sources,
            skills_user_id=user_id,
            skills_system_prompt=NOESIS_SKILLS_SYSTEM_PROMPT,
            memory=resolved_context.memory_sources,
            memory_system_prompt=NOESIS_MEMORY_SYSTEM_PROMPT,
            todo=True,
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
        run_id: Optional[str] = None,
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
                    run_id=run_id,
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
        run_id: Optional[str] = None,
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
                    run_id=run_id,
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
