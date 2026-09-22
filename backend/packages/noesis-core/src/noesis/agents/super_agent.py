"""SuperAgent - 通用超级智能体（filesystem + skills + web + 用户记忆）。"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncGenerator, Awaitable, Callable, Optional, TypeVar

from deepagents.backends.protocol import BackendProtocol
from langgraph.types import Command
from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from noesis.agents.backends import agent_sandbox_session, create_agent_backend
from noesis.paths import AGENT_MEMORY_AGENTS_FILE, AGENT_MEMORY_INDEX_FILE, AGENT_MEMORY_USER_FILE
from noesis.agents.base import BaseAgent, DEFAULT_RECURSION_LIMIT
from noesis.factory import build_noesis_middleware, create_noesis_agent
from noesis.agents.tools.ask_user import ask_user_tool, build_interrupt_on
from noesis.agents.middlewares.memory_write_middleware import MemoryWriteMiddleware
from noesis.agents.prompts import PromptProfile, build_prompt
from noesis.agents.prompts.memory import NOESIS_MEMORY_SYSTEM_PROMPT
from noesis.agents.prompts.super_agent import NOESIS_SKILLS_SYSTEM_PROMPT
from noesis.agents.skills import resolve_skill_sources_for_session
from noesis.agents.background import (
    AsyncSubagentToolsMiddleware,
    BackgroundTaskExecutor,
    BgNotifyMiddleware,
    SubagentRegistry,
    SubagentRole,
    assert_no_bg_task_tools,
)
from noesis.agents.background.shell.tools import replace_execute_tool
from noesis.agents.tools.fs_hints import augment_filesystem_tool_descriptions, guard_worker_filesystem_tools
from noesis.config.env import HitlConfig, SubagentConfig
from noesis.agents.tools import build_web_search_tools
from noesis.agents.tools.chat_attachment_tools import resolve_attachment_tools
from noesis.agents.tools.history_search_tool import build_history_search_tools
from noesis.agents.tools.kb_search_tool import build_kb_search_tools
from noesis.agents.tools.memory_tools import build_memory_tools
from noesis.runtime.logging import logger
from noesis.config.env import ChatAttachmentConfig
from noesis.memory.layout import ensure_user_memory_files
from noesis.agents.context import ContextResolver
from noesis.llm.factory import get_llm
from noesis.runtime.attachments.input_resolver import AttachmentInputResolver

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
    session_id: str = "",
    checkpointer=None,
):
    """编译后台 task-worker：独立上下文，供 BackgroundTaskExecutor 使用。

    worker 不交互、不审批（无人值守）：工具集不含 ask_user，execute 的
    危险命令经工具层确定性拒绝（guard_worker_filesystem_tools），拒绝
    事实随结果回流，主 Agent 可在主 run 中升级执行。
    """
    from langchain.agents import create_agent

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
        # 描述增强 + 危险命令拒绝（worker 无审批，见 docstring）
        filesystem_middleware_hook=lambda fm: (
            augment_filesystem_tool_descriptions(fm),
            guard_worker_filesystem_tools(fm),
        ),
    ))
    return create_agent(
        model,
        system_prompt=build_prompt(PromptProfile.SUPER_AGENT_SUB),
        tools=tools,
        middleware=middleware,
        name="task-worker",
        checkpointer=checkpointer,
    )


_T = TypeVar("_T")


async def _db_on_main_loop(
    factory: Callable[[], Awaitable[_T]], *, name: str
) -> _T:
    """DB 协程经主 loop 调度后再等待。

    pg_manager 连接池绑定主 loop，而子 Agent 回调可能在 executor 隔离
    loop 上被 await（与 ``_create_turn_run`` 同理——冷恢复曾因直连
    静默失败）。主 loop 未注册（评测/CLI 等单 loop 进程）时退回当前
    loop 直连：该场景下池本就绑定当前 loop，直连即正确路径。
    工厂参数而非协程：``run_on_main_loop`` 在主 loop 不可用时会关闭
    传入的协程，回退需要重新构造一个。
    """
    from noesis.runtime.main_loop import run_on_main_loop

    future = run_on_main_loop(factory(), name=name)
    if future is None:
        return await factory()
    return await asyncio.wrap_future(future)


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
        history_search_enabled: bool = True,
        compaction_enabled: bool = True,
        disable_hitl: bool = False,
        run_id: Optional[str] = None,
    ):
        ensure_user_memory_files(user_id)
        backend = await create_agent_backend(user_id, session_id)
        # backend 注入 web_fetch：超限页面全文落盘、模型可 read_file 续读
        web_tools = build_web_search_tools(backend=backend)
        tools = list(web_tools) + list(mcp_tools or [])
        # Agentic 召回：root run 装配检索工具（命中后合并回写 run.memory_context，
        # 作为抽取防自强化输入）；run_id/db 缺席时退化为纯只读检索
        tools.extend(build_memory_tools(user_id=user_id, run_id=run_id, db=db))
        # 原文层召回：会话历史检索（与 search_memory 蒸馏层成对，工具内开
        # 独立 DB 短事务，不依赖请求级 session 的存活窗口）；
        # history_search_enabled=False 供压缩评测构造「无会话检索」对照组
        if history_search_enabled:
            tools.extend(build_history_search_tools(user_id=user_id, session_id=session_id))
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
        ):
            tools = tools + await resolve_attachment_tools(
                session_id=session_id,
                user_id=user_id,
                file_list=file_list,
            )

        # 后台子 Agent（全异步 task）：主 Agent 经 AsyncSubagentToolsMiddleware 的
        # start/check 工具委派，子任务在进程内隔离 loop 跑，生命周期归属
        # session，跨 run 可收结果。worker 不携带后台任务工具自身（装配期
        # 断言，禁止递归委派）。worker 经角色工厂在隔离 loop 内惰性编译：
        # LLM 客户端与 checkpointer 连接池必须绑定隔离 loop（复用主 loop
        # 实例会 cross-loop 报错）。worker 的检索只读不写：召回清单只归
        # root run（防自强化输入），子 Agent 结论经父会话终态回流。
        # 会话历史检索工具同样只在主 loop：worker 内调用会撞 pg_manager
        # 主 loop 绑定的连接池（cross-loop 直连报错），且 worker 场景
        # （独立子任务）不需要跨 run 的会话原文召回
        # ask_user 同属 loop 绑定剔除：worker 无人值守不交互，歧义在结果中
        # 说明假设后继续（危险命令拒绝见 guard_worker_filesystem_tools）
        _loop_bound_tools = {"search_memory", "search_history", "search_sessions", "ask_user"}
        worker_tools = [
            tool for tool in tools if getattr(tool, "name", "") not in _loop_bound_tools
        ] + build_memory_tools(user_id=user_id)
        assert_no_bg_task_tools(worker_tools)

        # 捕获父 run 解析出的自定义模型快照（纯数据，跨线程安全）：
        # ContextVar 不跨线程，隔离 loop 里 get_llm 看不到它，自定义模型
        # 会被目录解析静默回退平台默认。worker 开局重放（见工厂体）。
        from noesis.llm.runtime_snapshot import (
            get_runtime_model_snapshot,
            replay_runtime_model_snapshot,
        )

        _worker_model_snapshot = (
            get_runtime_model_snapshot(model_id, purpose="chat") if model_id else None
        )

        async def _bg_worker_factory(model_id_override: str | None = None):
            from noesis.config.checkpointer import create_isolated_checkpointer

            # 重放快照供 worker 的 LLM 构建消费；覆盖的模型 id 与快照不一致
            # 时不重放（strict 解析大声失败），绝不用错模型
            replay_runtime_model_snapshot(
                _worker_model_snapshot,
                target_model_id=model_id_override or model_id,
            )

            return _compile_task_worker(
                # worker 专用 backend：/memory 只读（沙箱按 user+session 幂等
                # 复用，二次组装不产生新容器）；记忆更新由主 Agent 收小结后
                # 自行完成，避免「委派写记忆 → 连环审批 → 拒后重试」
                await create_agent_backend(user_id, session_id, memory_read_only=True),
                worker_tools,
                skill_sources,
                user_id=user_id,
                # 追加消息 可按 turn 切换模型：覆盖优先，否则沿用父 Agent 模型
                model_id=model_id_override or model_id,
                session_id=session_id,
                checkpointer=await create_isolated_checkpointer(),
            )

        def _cold_resolver(subagent_type, model_id):
            """冷恢复配方解析：按 descriptor 的 type/model 取角色 worker 工厂。

            追加消息 工厂由 executor 生成（user_id 来自 DB 事实，不闭包捕获
            装配期会话）；类型未注册返回 None（冷恢复按可诊断错误拒绝）。
            """
            role = subagent_registry.get(subagent_type or "")
            if role is None:
                return None
            return role.worker_factory

        bg_executor = BackgroundTaskExecutor(
            max_concurrent_per_session=SubagentConfig.max_concurrent_per_session,
            max_concurrent_global=SubagentConfig.max_concurrent_global,
            task_timeout_seconds=SubagentConfig.task_timeout_seconds,
            shell_task_timeout_seconds=SubagentConfig.shell_task_timeout_seconds,
            terminal_retention_seconds=SubagentConfig.terminal_retention_seconds,
            terminal_reclaim_max=SubagentConfig.terminal_reclaim_max,
            cold_resolver=_cold_resolver,
        )

        # 角色注册表：类型分发的唯一声明面（v1 单一 general，配方 = 既有
        # worker 工厂原样搬家，零行为变化）。未来种类在此注册各自的角色
        # 声明（prompt / 工具集 / 模型绑定闭包在各自 worker_factory 内）。
        subagent_registry = SubagentRegistry()
        subagent_registry.register(SubagentRole(
            name="general",
            description="通用子 Agent：多轮检索、调研、长命令等独立子任务",
            worker_factory=_bg_worker_factory,
        ))

        # 同步子 Agent（deepagents 原生 SubAgentMiddleware → task 工具）：
        # 子图跑在父 run 同一流内、父 Agent 阻塞等结果，适合需要立即拿到
        # 结果的子任务；长任务 / 并行仍走 start_task 后台路径。工具与
        # middleware 配方对齐后台 worker，但不带 ask_user——审批中断依赖
        # executor 转 排队任务，同步子图没有这条处理链。
        sync_subagent_model = get_llm(model_id=model_id)
        sync_subagent_tools = [
            tool for tool in worker_tools
            if getattr(tool, "name", "") != "ask_user"
        ]
        sync_subagents = [{
            "name": "general-purpose",
            "description": (
                "同步子 Agent：在独立上下文中执行多步子任务，调用期间父 Agent "
                "阻塞等待、结果当场返回。适合需要立即拿到结果的检索、调研类子任务；"
                "预计耗时较长或需与其它子任务并行时改用 start_async_task"
            ),
            "system_prompt": build_prompt(PromptProfile.SUPER_AGENT_SUB),
            "model": sync_subagent_model,
            "tools": sync_subagent_tools,
            "middleware": [
                *build_noesis_middleware(
                    profile="SUBAGENT",
                    model=sync_subagent_model,
                    model_id=model_id,
                    tools=sync_subagent_tools,
                    backend=backend,
                    skills=skill_sources,
                    skills_user_id=user_id,
                    skills_system_prompt=NOESIS_SKILLS_SYSTEM_PROMPT,
                    session_id=session_id,
                    filesystem_middleware_hook=augment_filesystem_tool_descriptions,
                ),
                # 同步子 Agent 与主 Agent 共享同一可写 /memory 路由：
                # 写入门卫 + 索引同步同样必须覆盖（见下方主栈注释）
                MemoryWriteMiddleware(user_id=user_id),
            ],
        }]

        def _filesystem_hook(fm):
            # 规则下沉（cwd/路径/读后改 → 工具描述）+ execute 后台化。
            # 顺序敏感：先增强描述再替换 execute（替换时保留原描述并追加后台提示）
            augment_filesystem_tool_descriptions(fm)
            replace_execute_tool(
                fm,
                executor=bg_executor,
                backend=backend,
                session_id=session_id,
                user_id=user_id,
            )

        async def _create_child_session(
            description: str,
            prompt: str | None = None,
            tool_call_id: str = "",
            subagent_type: str = "general",
            effective_model_id: str | None = None,
        ) -> dict[str, str]:
            # 工具可能在并行 tool-call 中同时创建多个子 Agent；不要复用请求级
            # AsyncSession，单独取连接保证每个 launch 有独立事务边界。
            async def _launch() -> dict[str, str]:
                from noesis.storage.postgres.manager import pg_manager

                async with pg_manager.get_async_session_context() as child_db:
                    from noesis.agents.background.ports import SubagentSessionPort

                    # The launch use case owns the child session, initial messages and
                    # standard AgentRun in one transaction.  Keep this callback small so
                    # the tool layer cannot accidentally create a second source of truth.
                    # description = 简短标题（会话标题）；prompt = 完整任务指令（首条用户消息）
                    # effective_model_id = 角色解析后的生效模型（绑定值或父模型）
                    launch = await SubagentSessionPort.launch(
                        parent_session_id=session_id,
                        user_id=user_id,
                        description=description,
                        prompt=prompt,
                        tool_call_id=tool_call_id or None,
                        model_id=effective_model_id,
                        subagent_type=subagent_type,
                        db=child_db,
                    )
                    return launch.to_dict()

            return await _db_on_main_loop(
                _launch, name=f"subagent-child-launch:{tool_call_id or description[:32]}")

        async def _delete_child_session(child_session_id: str) -> None:
            async def _delete() -> None:
                from noesis.storage.postgres.manager import pg_manager

                async with pg_manager.get_async_session_context() as child_db:
                    from noesis.agents.background.ports import SessionOpsPort

                    await SessionOpsPort.delete_session(child_session_id, user_id, db=child_db)

            await _db_on_main_loop(
                _delete, name=f"subagent-child-delete:{child_session_id}")

        async def _fail_child_run(run_id: str, error: str) -> None:
            async def _reject() -> None:
                from noesis.agents.background.ports import SubagentSessionPort

                await SubagentSessionPort.mark_launch_rejected(run_id, error)

            await _db_on_main_loop(_reject, name=f"subagent-run-reject:{run_id}")

        async def _create_turn_run(
            child_session_id: str,
            message: str,
            user_message_id: str | None = None,
        ) -> dict[str, str]:
            """冷恢复 / 链式 追加消息 的新 run 创建。

            经 run_on_main_loop 在主 loop 执行：pg_manager 连接池绑定主
            loop，而本工厂在 executor 隔离 loop 上被调用（send_message 冷
            恢复与运行中 追加消息链两处）——直连会触发 asyncpg 跨 loop
            连接错误，冷恢复曾因此静默失败（任务卡 RUNNING、追问无回复）。
            """
            from noesis.runtime.main_loop import run_on_main_loop
            from noesis.agents.background.ports import SubagentSessionPort
            from noesis.storage.postgres.manager import pg_manager

            async def _launch() -> dict[str, str]:
                async with pg_manager.get_async_session_context() as child_db:
                    launch = await SubagentSessionPort.create_turn_run(
                        session_id=child_session_id,
                        user_id=user_id,
                        message=message,
                        user_message_id=user_message_id,
                        db=child_db,
                    )
                    return launch.to_dict()

            future = run_on_main_loop(
                _launch(), name=f"subagent-turn-launch:{child_session_id}",
            )
            if future is None:
                raise RuntimeError("主 loop 不可用，追加消息 run 创建失败")
            return await asyncio.wrap_future(future)

        return create_noesis_agent(
            profile="SUPER_AGENT_QA",
            tools=tools,
            system_prompt=resolved_context.system_prompt,
            checkpointer=self.checkpointer,
            compaction_enabled=compaction_enabled,
            model=sync_subagent_model,
            subagents=sync_subagents,
            middleware=[
                # 子 Agent 工具面 + 任务身份 graph state（start_task 按
                # subagent_type 分发；类型清单注入 system prompt）
                AsyncSubagentToolsMiddleware(
                    registry=subagent_registry,
                    executor=bg_executor,
                    session_id=session_id,
                    user_id=user_id,
                    create_child_session=_create_child_session,
                    delete_child_session=_delete_child_session,
                    fail_child_run=_fail_child_run,
                    create_turn_run=_create_turn_run,
                    model_id=model_id,
                ),
                # run 内即时感知后台任务终态：下一次模型调用注入 [系统通知]
                BgNotifyMiddleware(session_id=session_id),
                # /memory 写入的引擎侧语义：白名单门卫（索引/journal 只读、
                # 条目命名校验）+ 条目写入后同步 MEMORY.md 索引行。worker 不挂——
                # 其 /memory 路由整体只读（见 _bg_worker_factory）
                MemoryWriteMiddleware(user_id=user_id),
            ],
            backend=backend,
            # execute 工具后台化（run_in_background，默认 false 前台零变化）；
            # 仅主 Agent 挂载——task-worker 保持前台 execute（禁止递归后台化）
            filesystem_middleware_hook=_filesystem_hook,
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
        history_search_enabled: bool = True,
        compaction_enabled: bool = True,
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
                    history_search_enabled=history_search_enabled,
                    compaction_enabled=compaction_enabled,
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
        history_search_enabled: bool = True,
        compaction_enabled: bool = True,
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
                    history_search_enabled=history_search_enabled,
                    compaction_enabled=compaction_enabled,
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
