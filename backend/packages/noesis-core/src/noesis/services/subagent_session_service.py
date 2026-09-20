"""子 Agent 会话用例。

子 Agent 在产品层是一组普通 ChatSession/ChatMessage/AgentRun，而不是
BackgroundTask 快照。执行器只持有本用例返回的运行身份，所有可见数据由
Postgres 中的标准模型承载。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, replace
from typing import Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession


from noesis.agents.background.jobs.state import run_status_to_task_status
from noesis.chat.runs.skeleton import (
    build_assistant_skeleton_row,
    build_queued_run_row,
    build_user_message_row,
)
from noesis.chat.runs import ASSISTANT_TERMINAL_STATUS, RunStatus
from noesis.config.env import StreamConfig
from noesis.errors.exceptions import ConflictException, NotFoundException
from noesis.runtime.logging import logger
from noesis.services.chat_service import ChatService
from noesis.services.run_recovery_service import mark_running_tools_unknown
from noesis.storage.postgres.models.chat import TAgentRun, TChatMessage, TChatSession
from noesis.agents.background.ports import configure_service_port


def _now_ms() -> int:
    return int(time.time() * 1000)


# subagent descriptor 当前版本：child session extra["subagent"] 的结构版本，
# 读取方按版本校验，不识别的版本按数据不完整处理（大声失败，不猜测回退）
SUBAGENT_DESCRIPTOR_VERSION = 1


def parse_subagent_descriptor(extra: Optional[dict]) -> Optional[dict]:
    """读取并校验 child session 的 subagent descriptor。

    返回 ``{"version", "type", "model"}``；无该键（历史会话或非 subagent
    会话）返回 None，结构或版本不符抛 ValueError。
    """
    if not isinstance(extra, dict):
        return None
    raw = extra.get("subagent")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("subagent descriptor 结构损坏（非对象）")
    version = raw.get("version")
    if version != SUBAGENT_DESCRIPTOR_VERSION:
        raise ValueError(f"subagent descriptor 版本不支持：{version}")
    if not isinstance(raw.get("type"), str) or not raw["type"]:
        raise ValueError("subagent descriptor 缺少 type 字段")
    return {
        "version": version,
        "type": raw["type"],
        "model": raw.get("model") if isinstance(raw.get("model"), str) else None,
    }


@dataclass(frozen=True)
class ChildSessionLaunch:
    session_id: str
    run_id: str
    user_message_id: str
    assistant_message_id: str
    profile_id: str
    created_by_tool_call_id: Optional[str] = None

    def to_dict(self) -> dict[str, str]:
        return {
            "child_session_id": self.session_id,
            "run_id": self.run_id,
            "user_message_id": self.user_message_id,
            "assistant_message_id": self.assistant_message_id,
            "profile_id": self.profile_id,
            "created_by_tool_call_id": self.created_by_tool_call_id,
        }


class SubagentSessionService:
    """创建/更新 child session 的唯一写入边界。"""

    PROFILE_ID = "task-worker"

    @classmethod
    def child_session_summary(cls, task: dict, *, parent_id: str) -> dict:
        """child 会话目录摘要（委托端口模块的单一构造点）。"""
        from noesis.agents.background.ports import child_session_summary

        return child_session_summary(task, parent_id=parent_id)

    @classmethod
    async def subscribe_remote_run_events(cls, run_id: str, user_id: str):
        """redis 模式：子会话 RunEvent 经 Run hub 订阅（task 4.9）。

        follower 上打开子代理会话页时，进程内投递内核不存在——hub 订阅
        bus 通道并以 DB 投影为 snapshot 对账（gap/静默丢失由 4.5 机制
        恢复）。订阅入口已完成 (run_id, user) 鉴权。
        """
        from noesis.services.run_service import _hub_registry

        return await _hub_registry().subscribe(run_id)

    @classmethod
    async def reconcile_orphaned_runs(cls, db: AsyncSession) -> int:
        """进程重启后将无法恢复的 child runs 收口为 error。

        子 Agent run 的唯一启动对账（通用 RunRecoveryService 显式排除
        origin=subagent）：executor 状态在进程内，重启即不可恢复，统一收口
        ERROR/SUBAGENT_PROCESS_RESTARTED；assistant 消息带最后投影内容并
        将运行中工具标为结果未知（与通用对账同规则，避免工具卡永久 running）。

        QUEUED 行不在收口集合：排队任务重启后应继续执行，由排队重建
        （list_queued_subagent_runs → executor.restore_queued）接手。被收口
        会话的 pending 追加消息行同事务翻转 dropped（任务已不可续，永无
        消费者；重启前已可续终态的任务不受影响）。
        """
        active = [
            RunStatus.RUNNING.value,
            RunStatus.RETRYING.value,
            RunStatus.HITL_PENDING.value,
        ]
        now = _now_ms()
        result = await db.execute(
            select(TAgentRun.id, TAgentRun.assistant_message_id).where(
                TAgentRun.origin == "subagent", TAgentRun.status.in_(active),
            )
        )
        orphaned = result.all()
        if not orphaned:
            return 0
        await db.execute(
            update(TAgentRun)
            .where(TAgentRun.id.in_([row.id for row in orphaned]))
            .values(
                status=RunStatus.ERROR.value,
                error_code="SUBAGENT_PROCESS_RESTARTED",
                user_error_message="后端进程重启，子 Agent 运行已中断",
                finish_reason="process_restart",
                finished_at=now,
                updated_at=now,
            )
        )
        message_result = await db.execute(
            select(TChatMessage).where(
                TChatMessage.id.in_([row.assistant_message_id for row in orphaned])
            )
        )
        for message in message_result.scalars().all():
            content = mark_running_tools_unknown(
                message.content if isinstance(message.content, dict) else None
            )
            await db.execute(
                update(TChatMessage)
                .where(TChatMessage.id == message.id)
                .values(status="error", content=content)
            )
        # 被收口会话的 pending 行翻转 dropped（任务已不可续）：仅仍处
        # pending 标记的行受影响，已采纳/已 dropped 的行幂等跳过
        orphaned_sessions = list({row.session_id for row in orphaned})
        pending_rows = (
            await db.execute(
                select(TChatMessage).where(
                    TChatMessage.session_id.in_(orphaned_sessions),
                    TChatMessage.deleted_at.is_(None),
                    cls._pending_expr(),
                )
            )
        ).scalars().all()
        for row in pending_rows:
            row.extra = {**(row.extra or {}), "pending_run": "dropped"}
        await db.commit()
        return len(orphaned)

    @classmethod
    async def list_queued_subagent_runs(cls, db: AsyncSession) -> list[dict]:
        """对账用：queued 状态的 child run 行（created_at 升序，排队重建源）。"""
        result = await db.execute(
            select(TAgentRun)
            .where(TAgentRun.origin == "subagent", TAgentRun.status == RunStatus.QUEUED.value)
            .order_by(TAgentRun.created_at.asc(), TAgentRun.id.asc())
        )
        return [
            {
                "run_id": run.id,
                "child_session_id": run.session_id,
                "created_at": run.created_at,
            }
            for run in result.scalars().all()
        ]

    @classmethod
    async def send_message(
        cls,
        *,
        session_id: str,
        user_id: str,
        message: str,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> dict:
        from noesis.agents.background.ports import ExecutorPort as BackgroundTaskExecutor

        task = await cls._owned_child(session_id, user_id)
        if task is None:
            raise NotFoundException(message="子会话不存在")
        # 校验折叠进 deliver_message 锁内前置：拒绝时丢弃已写的待定消息
        pending_user_message_id = await cls.create_pending_message(
            session_id=session_id,
            user_id=user_id,
            message=message,
            model_id=model_id,
            reasoning_effort=reasoning_effort,
        )
        try:
            # 冷恢复：响应前完成新 run 创建（run_id 权威）
            return await BackgroundTaskExecutor.deliver_message(
                session_id,
                message,
                user_message_id=pending_user_message_id,
                model_id=model_id,
                reasoning_effort=reasoning_effort,
            )
        except ValueError as exc:
            await cls._discard_pending_user_message(pending_user_message_id, user_id)
            raise ConflictException(message=str(exc)) from exc

    @classmethod
    async def collect_partial_output(cls, session_id: str, user_id: str) -> str:
        """从子会话全部 assistant 消息投影提取 text parts（部分成果回收的权威来源）。

        覆盖全部轮次（followup 链早轮）与硬杀场景（最后一次边界 persist 的投影）；
        按 message_sequence 顺序拼接，与消息流展示一致。
        """
        from sqlalchemy import select
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            rows = await db.execute(
                select(TChatMessage.content)
                .where(
                    TChatMessage.session_id == session_id,
                    TChatMessage.user_id == str(user_id),
                    TChatMessage.role == "assistant",
                    TChatMessage.deleted_at.is_(None),
                )
                .order_by(TChatMessage.message_sequence)
            )
            texts: list[str] = []
            for (content,) in rows.all():
                if not isinstance(content, dict):
                    continue
                for part in content.get("parts", []):
                    if (
                        isinstance(part, dict)
                        and part.get("type") == "text"
                        and part.get("content")
                    ):
                        texts.append(str(part["content"]))
            return "\n".join(texts).strip()

    @staticmethod
    async def _discard_pending_user_message(message_id: str, user_id: str) -> None:
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                update(TChatMessage)
                .where(TChatMessage.id == message_id, TChatMessage.user_id == str(user_id))
                .values(deleted_at=_now_ms())
            )
            await db.commit()

    @classmethod
    async def create_pending_message(
        cls,
        *,
        session_id: str,
        user_id: str,
        message: str,
        model_id: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        """写 pending user message 行（追加消息的 write-ahead 载体）。

        extra.pending_run=True 为待投递标记；turn 参数随行落库——排队期间
        参数不再只存内存（重启后冷恢复重载队列据此还原逐 turn 覆盖）。
        """
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            session = await ChatService.get_session_by_id(session_id, user_id=user_id, db=db)
            if session is None or session.kind != "subagent":
                raise NotFoundException(message="子会话不存在")
            now = _now_ms()
            message_id = str(uuid.uuid4())
            _, sequences = await ChatService.reserve_message_sequences(session_id, user_id, 1, db)
            extra: dict = {"origin": "subagent", "pending_run": True}
            turn_params: dict = {}
            if model_id:
                turn_params["model_id"] = model_id
            if reasoning_effort:
                turn_params["reasoning_effort"] = reasoning_effort
            if turn_params:
                extra["turn_params"] = turn_params
            db.add(build_user_message_row(
                message_id=message_id,
                session_id=session_id,
                user_id=str(user_id),
                text=message,
                extra=extra,
                message_sequence=sequences[0],
                created_at=now,
            ))
            session.updated_at = now
            await db.commit()
            return message_id

    # -- pending 行：容量计数 / dropped 翻转 / 重载 ----------------------

    @staticmethod
    def _pending_expr():
        """pending 标记的 JSON 查询表达式（->> 渲染 'true'，可靠区分 dropped）。"""
        return TChatMessage.extra["pending_run"].as_string() == "true"

    @staticmethod
    def _dropped_expr():
        return TChatMessage.extra["pending_run"].as_string() == "dropped"

    @classmethod
    async def count_pending_messages(cls, session_id: str) -> int:
        """该 child session 的 pending 行计数（追加消息队列容量判定）。"""
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            result = await db.execute(
                select(func.count())
                .select_from(TChatMessage)
                .where(
                    TChatMessage.session_id == session_id,
                    TChatMessage.deleted_at.is_(None),
                    cls._pending_expr(),
                )
            )
            return int(result.scalar_one() or 0)

    @classmethod
    async def flip_pending_message_dropped(cls, message_id: str) -> int:
        """单条 pending 行翻转 dropped（投递失败路径；幂等）。"""
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            row = (
                await db.execute(
                    select(TChatMessage).where(
                        TChatMessage.id == message_id,
                        TChatMessage.deleted_at.is_(None),
                        cls._pending_expr(),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return 0
            row.extra = {**(row.extra or {}), "pending_run": "dropped"}
            await db.commit()
            return 1

    @classmethod
    async def flip_pending_messages_dropped(cls, session_id: str) -> int:
        """该 child session 全部 pending 行翻转 dropped（不可续终态 / 对账）。"""
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            rows = (
                await db.execute(
                    select(TChatMessage).where(
                        TChatMessage.session_id == session_id,
                        TChatMessage.deleted_at.is_(None),
                        cls._pending_expr(),
                    )
                )
            ).scalars().all()
            for row in rows:
                row.extra = {**(row.extra or {}), "pending_run": "dropped"}
            if rows:
                await db.commit()
            return len(rows)

    @classmethod
    async def list_pending_message_rows(cls, session_id: str) -> list[dict]:
        """pending 行重载源（冷恢复重建条目时还原执行镜像队列）。"""
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            rows = (
                await db.execute(
                    select(TChatMessage)
                    .where(
                        TChatMessage.session_id == session_id,
                        TChatMessage.deleted_at.is_(None),
                        cls._pending_expr(),
                    )
                    .order_by(TChatMessage.message_sequence)
                )
            ).scalars().all()
            out = []
            for row in rows:
                extra = row.extra if isinstance(row.extra, dict) else {}
                params = extra.get("turn_params") if isinstance(extra.get("turn_params"), dict) else {}
                out.append({
                    "message_id": row.id,
                    "text": cls._text_of(row),
                    "model_id": params.get("model_id"),
                    "reasoning_effort": params.get("reasoning_effort"),
                })
            return out

    @staticmethod
    def _text_of(row: TChatMessage) -> str:
        content = row.content if isinstance(row.content, dict) else {}
        for part in content.get("parts") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                return str(part.get("content") or "")
        return ""

    @classmethod
    async def resume_hitl(
        cls,
        *,
        run_id: str,
        user_id: str,
        decisions: list,
        db: AsyncSession,
    ) -> TAgentRun:
        """子 Agent 无待审批操作：后台任务全自主运行（危险命令经工具层
        确定性拒绝，无人值守不挂审批）。保留入口仅为给出明确 409。"""
        run = await cls._get_owned_run(run_id, user_id, db)
        if run is None or run.origin != "subagent":
            raise NotFoundException(message="子 Agent run 不存在")
        raise ConflictException(message="子 Agent 任务无待审批操作")

    @classmethod
    async def stop_run(cls, *, run_id: str, user_id: str, db: AsyncSession) -> "RunSnapshot":
        """请求协作停止：立即返回受理快照（RunSnapshot 契约），不等待终态。

        终态经 bg-task / run.finished 事件推送并落库；即时取消路径（queued /
        已取消/排队任务 / shell）映射为 interrupted / stopped。受理态不写回
        DB——终态前的 DB 行保持原状态。
        """
        from noesis.chat.runs.models import RunSnapshot
        from noesis.services.run_service import RunService

        run = await cls._get_owned_run(run_id, user_id, db)
        if run is None or run.origin != "subagent":
            raise NotFoundException(message="子 Agent run 不存在")
        from noesis.agents.background.ports import ExecutorPort as BackgroundTaskExecutor

        try:
            # 带兜底：热集 miss（终态回收后）对 DB 已终态任务幂等返回快照，
            # 而非 409「任务不存在」（主规格「重复停止幂等」）
            accepted = await BackgroundTaskExecutor.cancel_with_fallback(run.session_id)
        except ValueError as exc:
            raise ConflictException(message=str(exc)) from exc
        snapshot = await RunService.get(run_id, user_id, db)
        accepted_status = str(accepted.get("status") or "")
        if accepted_status == "cancelled":
            return replace(snapshot, status=RunStatus.INTERRUPTED, finish_reason="stopped")
        return snapshot

    @staticmethod
    async def _get_owned_run(run_id: str, user_id: str, db: AsyncSession) -> TAgentRun | None:
        result = await db.execute(select(TAgentRun).where(TAgentRun.id == run_id, TAgentRun.user_id == str(user_id)))
        return result.scalar_one_or_none()

    @classmethod
    async def _wait_run(cls, db: AsyncSession, run_id: str, user_id: str, predicate) -> TAgentRun:
        import asyncio

        for _ in range(20):
            run = await cls._get_owned_run(run_id, user_id, db)
            if run is None or predicate(run):
                return run
            await asyncio.sleep(0.02)
            await db.rollback()
        return await cls._get_owned_run(run_id, user_id, db)

    @staticmethod
    async def _owned_child(session_id: str, user_id: str):
        """只认当前用户的 subagent 子会话；普通会话同样视为不存在（→404）。"""
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            session = await ChatService.get_session_by_id(session_id, user_id=user_id, db=db)
            if session is None or session.kind != 'subagent':
                return None
            return session

    @staticmethod
    def subscribe_run_events(run_id: str, user_id: str):
        from noesis.agents.background.ports import ExecutorPort

        return ExecutorPort.subscribe_run_events(run_id, user_id)

    @staticmethod
    def unsubscribe_run_events(run_id: str, queue) -> None:
        from noesis.agents.background.ports import ExecutorPort

        ExecutorPort.unsubscribe_run_events(run_id, queue)

    @staticmethod
    def get_run_event_history(run_id: str, after_sequence: int = 0) -> list[dict]:
        from noesis.agents.background.ports import ExecutorPort

        return ExecutorPort.get_run_event_history(run_id, after_sequence)

    @classmethod
    async def launch(
        cls,
        *,
        parent_session_id: str,
        user_id: str,
        description: str,
        prompt: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        model_id: Optional[str] = None,
        subagent_type: str = "general",
        db: AsyncSession,
    ) -> ChildSessionLaunch:
        parent = await ChatService.get_session_by_id(parent_session_id, user_id=user_id, db=db)
        if parent is None:
            raise NotFoundException(message="父会话不存在")
        parent_run_result = await db.execute(
            select(TAgentRun)
            .where(
                TAgentRun.session_id == parent_session_id,
                TAgentRun.user_id == str(user_id),
                TAgentRun.status.in_([
                    RunStatus.QUEUED.value,
                    RunStatus.RUNNING.value,
                    RunStatus.RETRYING.value,
                    RunStatus.HITL_PENDING.value,
                ]),
            )
            .order_by(TAgentRun.created_at.desc())
            .limit(1)
        )
        parent_run = parent_run_result.scalar_one_or_none()

        now = _now_ms()
        child_session_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        user_message_id = str(uuid.uuid4())
        assistant_message_id = str(uuid.uuid4())
        client_request_id = f"subagent:{run_id}"
        # description = 简短标题（会话标题/任务卡）；prompt = 完整任务指令（子 Agent 首条输入）
        task_text = (prompt or description).strip() or description.strip()
        digest = hashlib.sha256(
            json.dumps(
                {"parent_session_id": parent_session_id, "description": description, "prompt": task_text},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        child = ChatService.build_session(
            user_id=str(user_id),
            title=description or "子 Agent",
            parent_id=parent_session_id,
            kind="subagent",
            created_by_tool_call_id=tool_call_id,
            # 先不写 parent run FK。child session -> parent run -> child run
            # 与 message FK 同时 flush 时会形成循环依赖，必须先让 child 行落库。
            created_by_run_id=None,
            extra={
                "qa_type": "SUPER_AGENT_QA",
                "origin": "subagent",
                "agent_profile": cls.PROFILE_ID,
                # 版本化 subagent descriptor（独立子 dict + 显式字段）：
                # 重启后重建 worker 按 type+model 取角色配方，不回读父会话。
                # model = 该角色解析后的生效模型（绑定值或父模型）
                "subagent": {
                    "version": SUBAGENT_DESCRIPTOR_VERSION,
                    "type": subagent_type,
                    "model": model_id,
                },
                # worker 实际用生效模型编译：落库 extra.model_id 让子会话
                # 详情的模型选择器显示真实模型（与主会话 extra.model_id 同键）
                **({"model_id": model_id} if model_id else {}),
            },
            session_id=child_session_id,
            # 本事务直接写入 2 条消息（不走 reserve），序号 1/2 已被占用
            next_message_sequence=3,
            created_at=now,
        )
        user_message = build_user_message_row(
            message_id=user_message_id,
            session_id=child_session_id,
            user_id=str(user_id),
            text=task_text,
            extra={"origin": "subagent", "run_id": run_id},
            message_sequence=1,
            created_at=now,
        )
        assistant_message = build_assistant_skeleton_row(
            message_id=assistant_message_id,
            session_id=child_session_id,
            user_id=str(user_id),
            parent_id=user_message_id,
            extra={"origin": "subagent", "run_id": run_id, "profile_id": cls.PROFILE_ID},
            message_sequence=2,
            created_at=now,
        )
        run = build_queued_run_row(
            run_id=run_id,
            user_id=str(user_id),
            session_id=child_session_id,
            assistant_message_id=assistant_message_id,
            client_request_id=client_request_id,
            request_digest=digest,
            qa_type="SUPER_AGENT_QA",
            origin="subagent",
            created_at=now,
        )

        db.add(child)
        await db.flush()
        db.add(user_message)
        db.add(assistant_message)
        await db.flush()
        db.add(run)
        await db.flush()
        # child.created_by_run_id 指向创建它的父轮次；child 自身 run 不会伪装成父 run。
        if parent_run is not None:
            await db.execute(
                update(TChatSession)
                .where(TChatSession.id == child_session_id)
                .values(created_by_run_id=parent_run.id)
            )
        await db.execute(
            update(TChatSession)
            .where(TChatSession.id == parent_session_id, TChatSession.user_id == str(user_id))
            .values(updated_at=now)
        )
        await db.commit()
        logger.info(
            "created subagent child session parent={} child={} run={} profile={}",
            parent_session_id,
            child_session_id,
            run_id,
            cls.PROFILE_ID,
        )
        return ChildSessionLaunch(
            session_id=child_session_id,
            run_id=run_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            profile_id=cls.PROFILE_ID,
            created_by_tool_call_id=tool_call_id,
        )

    @classmethod
    async def mark_started(cls, run_id: str, started_at: Optional[int] = None) -> None:
        from noesis.storage.postgres.manager import pg_manager

        now = started_at or _now_ms()
        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                update(TAgentRun)
                .where(TAgentRun.id == run_id, TAgentRun.status == RunStatus.QUEUED.value)
                .values(status=RunStatus.RUNNING.value, started_at=now, updated_at=now)
            )
            await db.commit()

    @classmethod
    async def mark_launch_rejected(cls, run_id: str, error: str) -> None:
        """启动失败（子会话即将软删）：run 置 ERROR，避免 dispatcher claim 后重建上下文必然失败。

        并发超限已改为排队（executor._launch），此方法仅覆盖排队之外的
        启动失败路径。此时子会话与 assistant 消息即将被软删，不做消息终态化。
        """
        from noesis.storage.postgres.manager import pg_manager

        if not run_id:
            return
        now = _now_ms()
        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                update(TAgentRun)
                .where(TAgentRun.id == run_id, TAgentRun.status == RunStatus.QUEUED.value)
                .values(
                    status=RunStatus.ERROR.value,
                    error_code="SUBAGENT_LAUNCH_REJECTED",
                    user_error_message=error,
                    finish_reason="error",
                    finished_at=now,
                    updated_at=now,
                )
            )
            await db.commit()

    @classmethod
    async def create_turn_run(
        cls,
        *,
        session_id: str,
        user_id: str,
        message: str,
        user_message_id: Optional[str] = None,
        db: AsyncSession,
    ) -> ChildSessionLaunch:
        """为同一 child session 创建下一轮 user/assistant/run（原 create_followup_run）。

        采纳 pending 行：同一事务内清 pending_run 标记、盖 run_id（write-ahead
        的投递确认与 run 创建原子）。注意采纳是整覆写 extra——调用方须在
        本方法前从行上读走 turn_params（重载消费路径的约定）。
        """
        session = await ChatService.get_session_by_id(session_id, user_id=user_id, db=db)
        if session is None or session.kind != "subagent":
            raise NotFoundException(message="子会话不存在")
        now = _now_ms()
        run_id = str(uuid.uuid4())
        pending_message_id = user_message_id
        user_message_id = pending_message_id or str(uuid.uuid4())
        assistant_message_id = str(uuid.uuid4())
        if pending_message_id:
            _, sequences = await ChatService.reserve_message_sequences(session_id, user_id, 1, db)
            user_message = None
        else:
            _, sequences = await ChatService.reserve_message_sequences(session_id, user_id, 2, db)
            user_message = build_user_message_row(
                message_id=user_message_id,
                session_id=session_id,
                user_id=str(user_id),
                text=message,
                extra={"origin": "subagent", "run_id": run_id},
                message_sequence=sequences[0],
                created_at=now,
            )
        digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
        if user_message is None:
            await db.execute(
                update(TChatMessage)
                .where(TChatMessage.id == user_message_id, TChatMessage.user_id == str(user_id))
                .values(extra={"origin": "subagent", "run_id": run_id}, status="completed")
            )
        assistant_sequence = sequences[0] if pending_message_id else sequences[1]
        assistant_message = build_assistant_skeleton_row(
            message_id=assistant_message_id,
            session_id=session_id,
            user_id=str(user_id),
            parent_id=user_message_id,
            extra={"origin": "subagent", "run_id": run_id, "profile_id": cls.PROFILE_ID},
            message_sequence=assistant_sequence,
            created_at=now,
        )
        run = build_queued_run_row(
            run_id=run_id,
            user_id=str(user_id),
            session_id=session_id,
            assistant_message_id=assistant_message_id,
            client_request_id=f"subagent:{run_id}",
            request_digest=digest,
            qa_type="SUPER_AGENT_QA",
            origin="subagent",
            created_at=now,
        )
        if user_message is not None:
            db.add(user_message)
        db.add(assistant_message)
        await db.flush()
        db.add(run)
        await db.flush()
        session.updated_at = now
        await db.commit()
        return ChildSessionLaunch(
            session_id=session_id,
            run_id=run_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            profile_id=cls.PROFILE_ID,
            created_by_tool_call_id=None,
        )

    @classmethod
    async def persist_projection(
        cls,
        *,
        run_id: str,
        assistant_message_id: str,
        content: dict,
        sequence: int,
    ) -> None:
        """检查点落库：经 AgentRunRepository.save_checkpoint（与主链路同一事务实现）。

        executor 的 LangGraph checkpoint 仍用于恢复；产品读取只看这里的标准
        消息。sequence guard 让迟到的跨 loop 投影不能覆盖更新内容；guard
        拒绝时静默丢弃（迟到检查点）。DB 抖动按主链路同款超时有界重试。
        """
        from noesis.repositories.agent_run_repository import AgentRunRepository
        from noesis.storage.postgres.manager import pg_manager

        deadline = time.monotonic() + StreamConfig.persistence_timeout_seconds
        while True:
            try:
                async with pg_manager.get_async_session_context() as db:
                    stored = await AgentRunRepository(db).save_checkpoint(
                        run_id=run_id,
                        assistant_message_id=assistant_message_id,
                        sequence=sequence,
                        snapshot=content,
                        content=content,
                        attempt_id=1,
                        status=RunStatus.RUNNING,
                        finish_reason=None,
                        updated_at=_now_ms(),
                    )
                    if stored:
                        await db.commit()
                    else:
                        # guard 拒绝：迟到检查点，不覆盖更新的 DB 状态
                        await db.rollback()
                    return
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.2)



    @classmethod
    async def mark_terminal(
        cls,
        *,
        run_id: str,
        status: RunStatus,
        content: Optional[dict] = None,
        error: Optional[str] = None,
        finish_reason: Optional[str] = None,
        usage: Optional[dict] = None,
        model_calls: Optional[list] = None,
    ) -> None:
        """run 终态化并同步 assistant 消息。

        content=None 表示沿用 run 当前快照——超时/异常/取消等非正常终态
        必须走此语义，保留执行期间 persist_projection 积累的进度，
        不得用空 parts 覆盖（否则用户打开详情只能看到空白）。

        usage 为统一管道的本会话 usage 聚合（steps/llm_ms/tokens/cache），
        终态写入 message.extra.usage——与主链路同结构，子会话统计条据此重建。
        model_calls 为同源的每次模型调用明细，终态写入 message.extra.model_calls。
        """
        from noesis.repositories.agent_run_repository import AgentRunRepository
        from noesis.storage.postgres.manager import pg_manager

        if status not in {RunStatus.COMPLETED, RunStatus.ERROR, RunStatus.PARTIAL, RunStatus.INTERRUPTED}:
            raise ValueError(f"终态不合法: {status.value}")
        now = _now_ms()
        async with pg_manager.get_async_session_context() as db:
            run = await AgentRunRepository(db).get(run_id)
            if run is None:
                return
            if content is None:
                content = (
                    run.snapshot
                    if isinstance(run.snapshot, dict)
                    else {"version": 1, "parts": []}
                )
                # HITL 等待中的内部标记不得泄漏进 assistant 消息
                # （mark_waiting_approval 写入、mark_resumed 摘除；_hitl_usage
                #  审计种子同样只留在 run 快照，不入消息）
                if isinstance(content.get("_pending_hitl"), dict) or isinstance(
                    content.get("_hitl_usage"), dict
                ):
                    content = {
                        k: v for k, v in content.items()
                        if k not in ("_pending_hitl", "_hitl_usage")
                    }
            await AgentRunRepository(db).finalize(
                run_id=run_id,
                target=status,
                assistant_status=ASSISTANT_TERMINAL_STATUS[status],
                content=content,
                last_sequence=int(run.last_sequence or 0),
                snapshot=content,
                finished_at=now,
                finish_reason=finish_reason or status.value,
                # SUBAGENT_FAILED 只标注真失败（run ERROR）。timeout/cancelled
                # 是 PARTIAL 终态：error 文案（超时/取消说明）仍进
                # user_error_message 供详情展示，但不得标失败错误码
                error_code="SUBAGENT_FAILED" if status is RunStatus.ERROR else None,
                user_error_message=error,
                usage=usage,
                model_calls=model_calls,
            )
            await db.commit()


    # -- DB 投影与冷恢复事实（热集 miss 的查询兜底 / 队列重载源） --------

    _ACTIVE_RUN_STATUSES = (
        RunStatus.QUEUED.value,
        RunStatus.RUNNING.value,
        RunStatus.RETRYING.value,
        RunStatus.HITL_PENDING.value,
    )

    @classmethod
    async def db_task_projection(cls, task_ref: str) -> Optional[dict]:
        """任务 DB 投影：状态映射复用内存同源函数，结果/来源从落库内容派生。

        task_ref = bg_task_id（child session extra）或 child session id。
        多 run 的 child session 取活跃 run 优先、无活跃取最新 run 行。
        返回 BackgroundTask.to_dict() 同形 dict（附 undelivered_messages /
        retrieval_sources）；无 DB 事实返回 None。
        """
        async with pg_manager_scope() as db:
            session = await cls._find_child_session(task_ref, db)
            if session is None:
                return None
            return await cls._project_session(session, db)

    @classmethod
    async def list_db_task_projections(cls, session_id: str) -> list[dict]:
        """父会话维度的 DB 投影清单（任务目录 / list 兜底）。"""
        async with pg_manager_scope() as db:
            result = await db.execute(
                select(TChatSession).where(
                    TChatSession.parent_id == session_id,
                    TChatSession.kind == "subagent",
                    TChatSession.deleted_at.is_(None),
                )
            )
            out = []
            for session in result.scalars().all():
                projection = await cls._project_session(session, db)
                if projection is not None:
                    out.append(projection)
            return out

    @classmethod
    async def load_cold_task(cls, task_ref: str) -> Optional[dict]:
        """冷恢复全量事实：投影 + pending 行（执行镜像重载源）。"""
        async with pg_manager_scope() as db:
            session = await cls._find_child_session(task_ref, db)
            if session is None:
                return None
            projection = await cls._project_session(session, db)
            if projection is None:
                return None
            projection["pending"] = await cls.list_pending_message_rows(str(session.id))
            return projection

    @staticmethod
    async def _find_child_session(task_ref: str, db: AsyncSession) -> Optional[TChatSession]:
        result = await db.execute(
            select(TChatSession).where(
                TChatSession.kind == "subagent",
                TChatSession.deleted_at.is_(None),
                or_(
                    TChatSession.id == task_ref,
                    TChatSession.extra["bg_task_id"].as_string() == task_ref,
                ),
            )
        )
        return result.scalar_one_or_none()

    @classmethod
    async def _project_session(cls, session: TChatSession, db: AsyncSession) -> Optional[dict]:
        session_id = str(session.id)
        run_result = await db.execute(
            select(TAgentRun)
            .where(TAgentRun.session_id == session_id)
            .order_by(
                # 活跃 run 优先，无活跃取最新 run 行
                TAgentRun.status.in_(cls._ACTIVE_RUN_STATUSES).desc(),
                TAgentRun.created_at.desc(),
                TAgentRun.id.desc(),
            )
            .limit(1)
        )
        run = run_result.scalar_one_or_none()
        extra = session.extra if isinstance(session.extra, dict) else {}
        descriptor = parse_subagent_descriptor(extra)
        undelivered = (
            await db.execute(
                select(func.count())
                .select_from(TChatMessage)
                .where(
                    TChatMessage.session_id == session_id,
                    TChatMessage.deleted_at.is_(None),
                    cls._dropped_expr(),
                )
            )
        ).scalar_one()
        turn_count = (
            await db.execute(
                select(func.count())
                .select_from(TAgentRun)
                .where(TAgentRun.session_id == session_id)
            )
        ).scalar_one()
        projection: dict = {
            "task_id": extra.get("bg_task_id") or session_id,
            "child_session_id": session_id,
            "session_id": session.parent_id or "",
            "user_id": str(session.user_id),
            "description": session.title or "子 Agent",
            "kind": "subagent",
            "subagent_type": descriptor.get("type") if descriptor else None,
            "model_id": descriptor.get("model") if descriptor else None,
            "status": "queued",
            "run_id": None,
            "assistant_message_id": None,
            "result": None,
            "error": None,
            "started_at": None,
            "completed_at": None,
            "turn_count": int(turn_count or 0),
            "progress_count": 0,
            "undelivered_messages": int(undelivered or 0),
            "retrieval_sources": [],
            "created_at": session.created_at,
        }
        if run is None:
            return projection
        assistant = (
            await db.execute(
                select(TChatMessage).where(TChatMessage.id == run.assistant_message_id)
            )
        ).scalar_one_or_none()
        content = assistant.content if isinstance(assistant.content, dict) else {}
        from noesis.chat.event_mapping.retrieval import extract_deduped_sources

        projection.update({
            "run_id": run.id,
            "assistant_message_id": run.assistant_message_id,
            "status": run_status_to_task_status(run.status, run.finish_reason),
            "result": cls._text_of(assistant) if assistant is not None else None,
            "error": run.user_error_message,
            "started_at": (run.started_at / 1000) if run.started_at else None,
            "completed_at": (run.finished_at / 1000) if run.finished_at else None,
            "retrieval_sources": extract_deduped_sources(content) if content else [],
            "terminal_persist_exhausted": run.error_code == "TERMINAL_PERSIST_EXHAUSTED",
        })
        return projection

    @classmethod
    async def mark_terminal_persist_exhausted(cls, run_id: str) -> None:
        """终态落库重试耗尽的诊断位（run 行 error_code；不改状态不伪造终态）。"""
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            await db.execute(
                update(TAgentRun)
                .where(
                    TAgentRun.id == run_id,
                    TAgentRun.status.in_(cls._ACTIVE_RUN_STATUSES),
                )
                .values(error_code="TERMINAL_PERSIST_EXHAUSTED", updated_at=_now_ms())
            )
            await db.commit()


def pg_manager_scope():
    """pg 会话上下文的模块级转发（避免服务方法内重复 import）。"""
    from contextlib import asynccontextmanager

    from noesis.storage.postgres.manager import pg_manager

    return pg_manager.get_async_session_context()


configure_service_port(SubagentSessionService)

__all__ = ["ChildSessionLaunch", "SubagentSessionService"]
