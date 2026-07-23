"""用户定时任务 Service + cron 校验。"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.logging import logger
from constants.code_enum import IntentEnum
from models.scheduled_task_models import TUserScheduledTask

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


class ScheduledTaskService:
    @staticmethod
    async def list_tasks(db: AsyncSession, user_id: int | str) -> List[Dict[str, Any]]:
        uid = int(user_id)
        result = await db.execute(
            select(TUserScheduledTask)
            .where(TUserScheduledTask.user_id == uid)
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
                and_(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == uid)
            )
        )
        row = result.scalar_one_or_none()
        return _to_dict(row) if row else None

    @classmethod
    async def create_task(
        cls, db: AsyncSession, user_id: int | str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        uid = int(user_id)
        name = str(payload.get("name") or "").strip() or "未命名任务"
        cron_expr = str(payload.get("cron_expr") or "").strip()
        timezone = str(payload.get("timezone") or "Asia/Shanghai").strip()
        qa_type = str(payload.get("qa_type") or IntentEnum.SUPER_AGENT_QA.value[0])
        if qa_type not in _ALLOWED_QA:
            raise ValueError(f"不支持的 qa_type: {qa_type}")
        validate_cron_expr(cron_expr, timezone)
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
            session_binding=str(payload.get("session_binding") or "none"),
            delivery=str(payload.get("delivery") or "none"),
            next_run_at=compute_next_run_ms(cron_expr, timezone, after_ms=now),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return _to_dict(row)

    @classmethod
    async def update_task(
        cls,
        db: AsyncSession,
        user_id: int | str,
        task_id: str,
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        uid = int(user_id)
        result = await db.execute(
            select(TUserScheduledTask).where(
                and_(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == uid)
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
        now = _now_ms()
        row.next_run_at = compute_next_run_ms(row.cron_expr, row.timezone, after_ms=now)
        row.updated_at = now
        await db.commit()
        await db.refresh(row)
        return _to_dict(row)

    @staticmethod
    async def delete_task(db: AsyncSession, user_id: int | str, task_id: str) -> bool:
        uid = int(user_id)
        result = await db.execute(
            delete(TUserScheduledTask).where(
                and_(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == uid)
            )
        )
        await db.commit()
        return (result.rowcount or 0) > 0

    @classmethod
    async def set_enabled(
        cls, db: AsyncSession, user_id: int | str, task_id: str, enabled: bool
    ) -> Optional[Dict[str, Any]]:
        return await cls.update_task(db, user_id, task_id, {"enabled": enabled})

    @classmethod
    async def run_once(
        cls, db: AsyncSession, user_id: int | str, task_id: str
    ) -> Optional[Dict[str, Any]]:
        """手动触发：更新 last_* 与 next_run；实际 Agent 跑次由调度器/后台任务执行。"""
        uid = int(user_id)
        result = await db.execute(
            select(TUserScheduledTask).where(
                and_(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == uid)
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        now = _now_ms()
        try:
            await cls._execute_task(row)
            row.last_status = "success"
            row.last_error = None
        except Exception as exc:
            logger.exception("scheduled task run_once failed id={}", task_id)
            row.last_status = "error"
            row.last_error = str(exc)[:2000]
        row.last_run_at = now
        row.next_run_at = compute_next_run_ms(row.cron_expr, row.timezone, after_ms=now)
        row.updated_at = now
        await db.commit()
        await db.refresh(row)
        return _to_dict(row)

    @staticmethod
    async def _execute_task(row: TUserScheduledTask) -> None:
        """
        Isolated 跑次占位：记录日志，避免首期强依赖完整 SSE 链路。
        后续可接入 RunOrchestrator + SUPER_AGENT_QA。
        """
        logger.info(
            "cron execute task_id={} user_id={} qa_type={} prompt_len={}",
            row.id,
            row.user_id,
            row.qa_type,
            len(row.prompt or ""),
        )

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
