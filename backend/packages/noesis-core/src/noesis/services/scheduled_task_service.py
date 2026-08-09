"""用户定时任务 Service + cron 校验。"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from noesis.runtime.logging import logger
from noesis.config.code_enum import IntentEnum
from noesis.storage.postgres.models.scheduled_task import TUserScheduledTask
from noesis.storage.postgres.models.settings import TUserScheduledTaskRun

_ALLOWED_QA = {
    IntentEnum.COMMON_QA.value[0],
    IntentEnum.FAULT_OPERATION_QA.value[0],
    IntentEnum.TEST_CASE_QA.value[0],
    IntentEnum.SUPER_AGENT_QA.value[0],
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def validate_cron_expr(expr: str, timezone: str = "Asia/Shanghai") -> None:
    expr = (expr or "").strip()
    if not expr:
        raise ValueError("cron_expr 不能为空")
    try:
        tz = ZoneInfo(timezone)
    except Exception as e:
        raise ValueError(f"非法 timezone: {timezone}") from e
    try:
        croniter(expr, datetime.now(tz))
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"非法 cron 表达式: {expr}") from e


def compute_next_run_ms(cron_expr: str, timezone: str, *, after_ms: Optional[int] = None) -> int:
    tz = ZoneInfo(timezone)
    base = datetime.fromtimestamp((after_ms or _now_ms()) / 1000.0, tz=tz)
    nxt = croniter(cron_expr, base).get_next(datetime)
    return int(nxt.timestamp() * 1000)


def _to_dict(row: TUserScheduledTask) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "cron_expr": row.cron_expr,
        "timezone": row.timezone,
        "enabled": bool(row.enabled),
        "qa_type": row.qa_type,
        "prompt": row.prompt,
        "session_binding": row.session_binding,
        "delivery": row.delivery,
        "last_run_at": row.last_run_at,
        "next_run_at": row.next_run_at,
        "last_status": row.last_status,
        "last_error": row.last_error,
        "disabled_reason": row.disabled_reason,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def cron_summary(cron_expr: str, timezone: str) -> str:
    validate_cron_expr(cron_expr, timezone)
    parts = cron_expr.split()
    if len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit() and parts[2:] == ["*", "*", "*"]:
        return f"每天 {int(parts[1]):02d}:{int(parts[0]):02d}（{timezone}）"
    if len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit() and parts[2:4] == ["*", "*"] and parts[4].isdigit():
        return f"每周 {parts[4]} {int(parts[1]):02d}:{int(parts[0]):02d}（{timezone}）"
    return f"按 cron {cron_expr} 执行（{timezone}）"


def _run_to_dict(row: TUserScheduledTaskRun) -> Dict[str, Any]:
    duration = None
    if row.started_at is not None and row.finished_at is not None:
        duration = max(0, row.finished_at - row.started_at)
    return {
        "id": row.id, "task_id": row.task_id, "status": row.status,
        "trigger_source": row.trigger_source, "retry_of": row.retry_of,
        "session_id": row.session_id, "result_summary": row.result_summary,
        "error_category": row.error_category, "error_message": row.error_message,
        "delivery_result": row.delivery_result, "started_at": row.started_at,
        "finished_at": row.finished_at, "duration_ms": duration, "created_at": row.created_at,
    }


class ScheduledTaskService:
    @staticmethod
    async def _deliver_run_notification(row: TUserScheduledTask, run: TUserScheduledTaskRun) -> dict:
        """把自动化结果送到既有 Web 运行记录或指定通讯通道。"""
        if row.delivery == "web_notify":
            # 运行记录本身就是 Web 通知表面，终态提交后可由设置页读取。
            return {"status": "delivered", "target": row.delivery, "surface": "web"}

        channel_id = row.delivery.removeprefix("channel:")
        correlation_id = str(uuid.uuid4())
        try:
            import asyncio

            from noesis.domain.chat.delivery.channel_health import channel_health
            from noesis.domain.chat.delivery.telegram.client import TelegramBotClient
            from noesis.services.messaging_channel_service import MessagingChannelService

            cfg = MessagingChannelService.get_runtime_channel(row.user_id, channel_id)
            if not cfg.enabled or not cfg.bot_token or not cfg.pairing_chat_id:
                return {
                    "status": "failed", "target": row.delivery,
                    "error_category": "configuration", "correlation_id": correlation_id,
                }
            message = (
                f"自动化任务已完成：{run.result_summary or '可在设置页查看运行详情'}"
                if run.status == "succeeded"
                else "自动化任务执行失败，请前往设置页查看运行详情。"
            )
            client = TelegramBotClient(cfg.bot_token, timeout=10)
            try:
                result = await asyncio.wait_for(client.send_message(cfg.pairing_chat_id, message), timeout=12)
            finally:
                await client.aclose()
            channel_health.report_activity(row.user_id, channel_id, "outbound", "succeeded")
            return {
                "status": "delivered", "target": row.delivery, "surface": "channel",
                "external_message_id": str(result.get("message_id") or "") or None,
                "correlation_id": correlation_id,
            }
        except TimeoutError:
            return {
                "status": "failed", "target": row.delivery,
                "error_category": "timeout", "correlation_id": correlation_id,
            }
        except Exception:
            logger.exception("scheduled task notification failed task_id={} run_id={}", row.id, run.id)
            return {
                "status": "failed", "target": row.delivery,
                "error_category": "delivery", "correlation_id": correlation_id,
            }

    @staticmethod
    async def _validate_targets(db: AsyncSession, user_id: int, session_binding: str, delivery: str) -> None:
        if session_binding != "none":
            if not session_binding.startswith("session:") or not session_binding.removeprefix("session:").strip():
                raise ValueError("session_binding 须为 none 或 session:{id}")
            from noesis.services.chat_service import ChatService
            session = await ChatService.get_session_by_id(session_binding.removeprefix("session:"), user_id=user_id, db=db)
            if session is None:
                raise ValueError("绑定会话不存在或不属于当前用户")
        if delivery not in {"none", "web_notify"}:
            if not delivery.startswith("channel:") or not delivery.removeprefix("channel:").strip():
                raise ValueError("delivery 须为 none、web_notify 或 channel:{id}")
            from noesis.services.messaging_channel_service import MessagingChannelService
            channel_ids = {item["channel_id"] for item in MessagingChannelService.list_channels(user_id)}
            if delivery.removeprefix("channel:") not in channel_ids:
                raise ValueError("投递通道不存在或不属于当前用户")

    @staticmethod
    async def list_tasks(db: AsyncSession, user_id: int | str) -> List[Dict[str, Any]]:
        uid = int(user_id)
        result = await db.execute(
            select(TUserScheduledTask)
            .where(TUserScheduledTask.user_id == uid, TUserScheduledTask.deleted_at.is_(None))
            .order_by(TUserScheduledTask.created_at.desc())
        )
        return [_to_dict(r) for r in result.scalars().all()]

    @staticmethod
    async def get_task(
        db: AsyncSession, user_id: int | str, task_id: str
    ) -> Optional[Dict[str, Any]]:
        uid = int(user_id)
        result = await db.execute(
            select(TUserScheduledTask).where(
                and_(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == uid, TUserScheduledTask.deleted_at.is_(None))
            )
        )
        row = result.scalar_one_or_none()
        return _to_dict(row) if row else None

    @classmethod
    async def create_task(
        cls, db: AsyncSession, user_id: int | str, payload: Dict[str, Any], *, commit: bool = True
    ) -> Dict[str, Any]:
        uid = int(user_id)
        name = str(payload.get("name") or "").strip() or "未命名任务"
        cron_expr = str(payload.get("cron_expr") or "").strip()
        timezone = str(payload.get("timezone") or "Asia/Shanghai").strip()
        qa_type = str(payload.get("qa_type") or IntentEnum.SUPER_AGENT_QA.value[0])
        if qa_type not in _ALLOWED_QA:
            raise ValueError(f"不支持的 qa_type: {qa_type}")
        validate_cron_expr(cron_expr, timezone)
        session_binding = str(payload.get("session_binding") or "none")
        delivery = str(payload.get("delivery") or "none")
        await cls._validate_targets(db, uid, session_binding, delivery)
        now = _now_ms()
        row = TUserScheduledTask(
            id=str(uuid.uuid4()),
            user_id=uid,
            name=name,
            cron_expr=cron_expr,
            timezone=timezone,
            enabled=bool(payload.get("enabled", True)),
            qa_type=qa_type,
            prompt=str(payload.get("prompt") or ""),
            session_binding=session_binding,
            delivery=delivery,
            next_run_at=compute_next_run_ms(cron_expr, timezone, after_ms=now),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        if commit:
            await db.commit()
            await db.refresh(row)
        else:
            await db.flush()
        return _to_dict(row)

    @classmethod
    async def update_task(
        cls,
        db: AsyncSession,
        user_id: int | str,
        task_id: str,
        payload: Dict[str, Any],
        *,
        commit: bool = True,
    ) -> Optional[Dict[str, Any]]:
        uid = int(user_id)
        result = await db.execute(
            select(TUserScheduledTask).where(
                and_(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == uid, TUserScheduledTask.deleted_at.is_(None))
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        if "name" in payload and payload["name"] is not None:
            row.name = str(payload["name"]).strip() or row.name
        if "cron_expr" in payload and payload["cron_expr"] is not None:
            row.cron_expr = str(payload["cron_expr"]).strip()
        if "timezone" in payload and payload["timezone"] is not None:
            row.timezone = str(payload["timezone"]).strip()
        if "enabled" in payload and payload["enabled"] is not None:
            row.enabled = bool(payload["enabled"])
            if row.enabled:
                row.disabled_reason = None
        if "qa_type" in payload and payload["qa_type"] is not None:
            qa = str(payload["qa_type"])
            if qa not in _ALLOWED_QA:
                raise ValueError(f"不支持的 qa_type: {qa}")
            row.qa_type = qa
        if "prompt" in payload and payload["prompt"] is not None:
            row.prompt = str(payload["prompt"])
        if "session_binding" in payload and payload["session_binding"] is not None:
            row.session_binding = str(payload["session_binding"])
        if "delivery" in payload and payload["delivery"] is not None:
            row.delivery = str(payload["delivery"])
        validate_cron_expr(row.cron_expr, row.timezone)
        await cls._validate_targets(db, uid, row.session_binding, row.delivery)
        now = _now_ms()
        row.next_run_at = compute_next_run_ms(row.cron_expr, row.timezone, after_ms=now)
        row.updated_at = now
        if commit:
            await db.commit()
            await db.refresh(row)
        else:
            await db.flush()
        return _to_dict(row)

    @staticmethod
    async def delete_task(db: AsyncSession, user_id: int | str, task_id: str) -> bool:
        uid = int(user_id)
        result = await db.execute(update(TUserScheduledTask).where(
            and_(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == uid, TUserScheduledTask.deleted_at.is_(None))
        ).values(enabled=False, deleted_at=_now_ms(), updated_at=_now_ms()))
        await db.commit()
        return (result.rowcount or 0) > 0

    @classmethod
    async def set_enabled(
        cls, db: AsyncSession, user_id: int | str, task_id: str, enabled: bool
    ) -> Optional[Dict[str, Any]]:
        return await cls.update_task(db, user_id, task_id, {"enabled": enabled})

    @classmethod
    async def run_once(
        cls, db: AsyncSession, user_id: int | str, task_id: str, idempotency_key: str | None = None
    ) -> Optional[Dict[str, Any]]:
        """手动触发并记录 immutable run。"""
        uid = int(user_id)
        result = await db.execute(
            select(TUserScheduledTask).where(
                and_(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == uid, TUserScheduledTask.deleted_at.is_(None))
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        run = await cls.execute_with_record(db, row, trigger_source="manual", idempotency_key=idempotency_key or str(uuid.uuid4()))
        now = _now_ms()
        row.last_status = run.status
        row.last_error = run.error_message
        row.last_run_at = now
        row.next_run_at = compute_next_run_ms(row.cron_expr, row.timezone, after_ms=now)
        row.updated_at = now
        await db.commit()
        await db.refresh(row)
        payload = _to_dict(row)
        payload["run"] = _run_to_dict(run)
        return payload

    @staticmethod
    async def _execute_task(row: TUserScheduledTask) -> None:
        """经现有 headless RunOrchestrator 执行，不另建 Agent 调用路径。"""
        from noesis.services.channel_run_service import run_channel_agent

        session_id = row.session_binding.removeprefix("session:") if row.session_binding.startswith("session:") else str(uuid.uuid4())
        return await run_channel_agent(user_id=row.user_id, session_id=session_id, query=row.prompt, qa_type=row.qa_type, origin="automation", channel_type="automation")

    @classmethod
    async def execute_with_record(cls, db: AsyncSession, row: TUserScheduledTask, *, trigger_source: str, idempotency_key: str, retry_of: str | None = None) -> TUserScheduledTaskRun:
        existing_result = await db.execute(select(TUserScheduledTaskRun).where(TUserScheduledTaskRun.user_id == row.user_id, TUserScheduledTaskRun.idempotency_key == idempotency_key))
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            return existing
        now = _now_ms()
        run = TUserScheduledTaskRun(id=str(uuid.uuid4()), task_id=row.id, user_id=row.user_id, status="queued", trigger_source=trigger_source, retry_of=retry_of, idempotency_key=idempotency_key, created_at=now)
        db.add(run)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raced_result = await db.execute(select(TUserScheduledTaskRun).where(TUserScheduledTaskRun.user_id == row.user_id, TUserScheduledTaskRun.idempotency_key == idempotency_key))
            raced = raced_result.scalar_one_or_none()
            if raced is not None:
                return raced
            raise
        await db.refresh(run)
        run.status = "running"
        run.started_at = _now_ms()
        await db.commit()
        await db.refresh(run)
        await db.refresh(row)
        try:
            result = await cls._execute_task(row)
            run.status = "succeeded"
            run.session_id = getattr(result, "session_id", None)
            run.result_summary = str(getattr(result, "plain_text", "") or "")[:1000]
            run.delivery_result = {"status": "not_requested" if row.delivery == "none" else "pending", "target": row.delivery}
        except Exception:
            logger.exception("scheduled task execute failed id={} run_id={}", row.id, run.id)
            run.status = "failed"
            run.error_category = "execution"
            run.error_message = "任务执行失败，请根据关联记录重试或检查配置"
            run.delivery_result = {"status": "not_attempted", "target": row.delivery}
        if row.delivery != "none":
            from noesis.services.notification_preference_service import NotificationPreferenceService
            event_type = "automation.succeeded" if run.status == "succeeded" else "automation.failed"
            surface = "web" if row.delivery == "web_notify" else "channel"
            if not await NotificationPreferenceService.should_notify(db, row.user_id, event_type, surface):
                run.delivery_result = {"status": "suppressed", "target": row.delivery}
            else:
                run.delivery_result = await cls._deliver_run_notification(row, run)
        run.finished_at = _now_ms()
        await db.commit()
        await db.refresh(run)
        return run

    @staticmethod
    async def list_runs(db: AsyncSession, user_id: int | str, task_id: str, page: int, page_size: int) -> dict:
        uid = int(user_id)
        task = await ScheduledTaskService.get_task(db, uid, task_id)
        if task is None:
            # 已删除任务仍允许查看其历史，但不得跨用户。
            count_task = await db.execute(select(func.count()).select_from(TUserScheduledTask).where(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == uid))
            if int(count_task.scalar_one()) == 0:
                return {"items": [], "total": 0, "not_found": True}
        total_result = await db.execute(select(func.count()).select_from(TUserScheduledTaskRun).where(TUserScheduledTaskRun.user_id == uid, TUserScheduledTaskRun.task_id == task_id))
        rows_result = await db.execute(select(TUserScheduledTaskRun).where(TUserScheduledTaskRun.user_id == uid, TUserScheduledTaskRun.task_id == task_id).order_by(TUserScheduledTaskRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
        return {"items": [_run_to_dict(row) for row in rows_result.scalars().all()], "total": int(total_result.scalar_one()), "page": page, "page_size": page_size}

    @staticmethod
    async def get_run(db: AsyncSession, user_id: int | str, run_id: str) -> TUserScheduledTaskRun | None:
        result = await db.execute(select(TUserScheduledTaskRun).where(TUserScheduledTaskRun.id == run_id, TUserScheduledTaskRun.user_id == int(user_id)))
        return result.scalar_one_or_none()

    @classmethod
    async def retry_run(cls, db: AsyncSession, user_id: int | str, run_id: str, idempotency_key: str) -> dict | None:
        old = await cls.get_run(db, user_id, run_id)
        if old is None:
            return None
        if old.status not in {"failed", "cancelled"}:
            raise ValueError("只有失败或已取消的运行可以重试")
        result = await db.execute(select(TUserScheduledTask).where(TUserScheduledTask.id == old.task_id, TUserScheduledTask.user_id == int(user_id), TUserScheduledTask.deleted_at.is_(None)))
        task = result.scalar_one_or_none()
        if task is None:
            raise ValueError("任务已删除，无法重试")
        run = await cls.execute_with_record(db, task, trigger_source="retry", idempotency_key=idempotency_key, retry_of=old.id)
        return _run_to_dict(run)

    @staticmethod
    async def cleanup_runs(db: AsyncSession, user_id: int | str, *, retention_days: int = 30, max_records: int = 1000) -> int:
        uid = int(user_id)
        cutoff = _now_ms() - retention_days * 24 * 60 * 60 * 1000
        ids_result = await db.execute(
            select(TUserScheduledTaskRun.id)
            .where(TUserScheduledTaskRun.user_id == uid)
            .order_by(TUserScheduledTaskRun.created_at.desc())
        )
        all_ids = list(ids_result.scalars().all())
        old_result = await db.execute(
            select(TUserScheduledTaskRun.id).where(
                TUserScheduledTaskRun.user_id == uid,
                TUserScheduledTaskRun.created_at < cutoff,
            )
        )
        delete_ids = set(old_result.scalars().all()) | set(all_ids[max_records:])
        if not delete_ids:
            return 0
        result = await db.execute(delete(TUserScheduledTaskRun).where(TUserScheduledTaskRun.id.in_(delete_ids)))
        await db.commit()
        return int(result.rowcount or 0)

    @staticmethod
    async def disable_session_bound_tasks(
        db: AsyncSession, user_id: int | str, session_id: str, *, reason: str
    ) -> int:
        uid = int(user_id)
        binding = f"session:{session_id}"
        now = _now_ms()
        result = await db.execute(
            update(TUserScheduledTask)
            .where(
                and_(
                    TUserScheduledTask.user_id == uid,
                    TUserScheduledTask.session_binding == binding,
                    TUserScheduledTask.enabled.is_(True),
                )
            )
            .values(
                enabled=False,
                disabled_reason=reason[:300],
                updated_at=now,
            )
        )
        await db.commit()
        return int(result.rowcount or 0)

    @staticmethod
    async def delete_all_for_user(db: AsyncSession, user_id: int | str) -> int:
        uid = int(user_id)
        result = await db.execute(
            delete(TUserScheduledTask).where(TUserScheduledTask.user_id == uid)
        )
        await db.commit()
        return int(result.rowcount or 0)

    @staticmethod
    async def claim_due_tasks(db: AsyncSession, *, limit: int = 20) -> List[TUserScheduledTask]:
        """抢占到期任务（Postgres FOR UPDATE SKIP LOCKED）。"""
        now = _now_ms()
        result = await db.execute(
            select(TUserScheduledTask)
            .where(
                and_(
                    TUserScheduledTask.enabled.is_(True),
                    TUserScheduledTask.next_run_at.is_not(None),
                    TUserScheduledTask.next_run_at <= now,
                    TUserScheduledTask.deleted_at.is_(None),
                )
            )
            .order_by(TUserScheduledTask.next_run_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list(result.scalars().all())
        for row in rows:
            # 先推进 next_run，避免其它 worker 重复抢
            row.next_run_at = compute_next_run_ms(row.cron_expr, row.timezone, after_ms=now)
            row.updated_at = now
        if rows:
            await db.commit()
            for row in rows:
                await db.refresh(row)
        return rows
