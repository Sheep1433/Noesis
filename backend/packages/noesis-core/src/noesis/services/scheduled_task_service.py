"""用户定时任务 Service + cron 校验。"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from noesis.ids import now_ms
from noesis.runtime.logging import logger
from noesis.config.code_enum import IntentEnum
from noesis.storage.postgres.models.scheduled_task import TUserScheduledTask
from noesis.storage.postgres.models.settings import TUserScheduledTaskRun

_ALLOWED_QA = {
    IntentEnum.COMMON_QA.value[0],
    IntentEnum.FAULT_OPERATION_QA.value[0],
    IntentEnum.SUPER_AGENT_QA.value[0],
}

# 定时任务无人值守模式前缀：注入到用户填写的 prompt 之前，约束 agent 自主完成、不等待用户输入。
# 仅在定时执行路径注入；用户手动续聊走网页路径，不受此约束、HITL 照常。
_AUTOMATION_MODE_PROMPT = """<automation_mode>
本次为定时任务自动执行，无人值守。请遵守：
- 直接执行下方任务指令，不要寒暄、不要反问、不要等待用户输入或确认。
- 缺少非关键参数时用合理默认值推进；仅当缺少关键信息致使任务完全无法执行时，输出简短说明并结束。
- 自我验证关键结论与产物；工具失败如实报告，不编造。
- 输出应完整可存档（用户之后会查看本次结果），重要事实附可追溯来源。
</automation_mode>

"""

# 自然语言解析允许的频率映射，用于把中文习惯表达归一成 cron。
_WEEKDAY_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_WEEKDAY_EN = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 7}


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit]


def _extract_json_object(content: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出里提取首个 JSON 对象，容许包裹在代码块或说明文字中。"""
    text = content or ""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


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
    base = datetime.fromtimestamp((after_ms or now_ms()) / 1000.0, tz=tz)
    nxt = croniter(cron_expr, base).get_next(datetime)
    return int(nxt.timestamp() * 1000)


def _to_dict(row: TUserScheduledTask) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "cron_expr": row.cron_expr,
        "summary": cron_summary(row.cron_expr, row.timezone),
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
    if len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit() and parts[2:4] == ["*", "*"]:
        days = parts[4].split(",")
        if days and all(day.isdigit() and 0 <= int(day) <= 7 for day in days):
            labels = "、".join(f"周{['日', '一', '二', '三', '四', '五', '六'][int(day) % 7]}" for day in days)
            return f"{labels} {int(parts[1]):02d}:{int(parts[0]):02d}（{timezone}）"
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

            from noesis.chat.delivery.channel_health import channel_health
            from noesis.chat.delivery.telegram.client import TelegramBotClient
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
    async def _provision_bound_session(
        db: AsyncSession, user_id: str, title: str, qa_type: str
    ) -> str:
        """预建一个空会话供定时任务绑定，所有运行结果追加进同一线程。"""
        from noesis.services.chat_service import ChatService

        session = await ChatService.create_session(
            user_id=str(user_id),
            title=title or None,
            extra={"qa_type": qa_type, "origin": "automation"},
            db=db,
        )
        return session.id

    @staticmethod
    async def _validate_targets(db: AsyncSession, user_id: str, session_binding: str, delivery: str) -> None:
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
    async def list_tasks(db: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
        uid = str(user_id)
        result = await db.execute(
            select(TUserScheduledTask)
            .where(TUserScheduledTask.user_id == uid, TUserScheduledTask.deleted_at.is_(None))
            .order_by(TUserScheduledTask.created_at.desc())
        )
        return [_to_dict(r) for r in result.scalars().all()]

    @staticmethod
    async def get_task(
        db: AsyncSession, user_id: str, task_id: str
    ) -> Optional[Dict[str, Any]]:
        uid = str(user_id)
        result = await db.execute(
            select(TUserScheduledTask).where(
                and_(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == uid, TUserScheduledTask.deleted_at.is_(None))
            )
        )
        row = result.scalar_one_or_none()
        return _to_dict(row) if row else None

    @staticmethod
    def _llm_error_hint(exc: Exception, model_target: str = "平台默认模型") -> str:
        """LLM 调用失败的用户面提示：不含 SDK 异常类名，按类型给可操作指向。"""
        text = str(exc)
        name = type(exc).__name__
        if "Authentication" in name or "401" in text or "INVALID_TOKEN" in text:
            return f"模型认证失败，请检查 {model_target} 的 API 凭据配置"
        if "RateLimit" in name or "429" in text:
            return "模型限流，请稍后重试"
        if "Connection" in name or "Connect" in name:
            return "模型服务连接失败，请检查网络与网关地址"
        return "模型不可用，请稍后重试或联系管理员检查模型配置"

    @classmethod
    async def parse_natural_language(cls, text: str, *, user_id: str, db: Any) -> Dict[str, Any]:
        """把自然语言（如「每周一早上9点收集网上资料整理AI Agent最新进展」）解析成定时任务草稿。

        仅产出 name / cron_expr / prompt，qa_type 固定 SuperAgent、时区固定 Asia/Shanghai、单任务单会话、不投递。
        后端用 validate_cron_expr 二次校验。模型解析与任务执行同链路：用户设置的默认模型优先，
        无则平台默认——解析与执行因此用同一个模型。LLM 调用失败（限流/配置）时原样暴露错误，不静默兜底。
        """
        raw = (text or "").strip()
        if not raw:
            raise ValueError("请输入任务描述")
        prompt = (
            "你是定时任务解析器。把用户的一句话解析成结构化定时任务，并把模糊意图扩写成具体可执行的指令。"
            "只输出 JSON，不要任何解释。字段："
            'name(任务名,<=30字),cron_expr(标准5字段cron,分钟在前),'
            'prompt(要执行的指令,在用户原意基础上扩写：明确要收集/处理的具体内容维度、'
            '信息来源方向、关注时间范围、输出结构与要点,让 Agent 拿到就能直接执行,不要泛泛而谈)。'
            "时间用24小时制；「每周一9点」→'0 9 * * 1'；「每天8:30」→'30 8 * * *'。"
            f"\n用户输入：{raw}"
        )
        from noesis.llm.factory import get_llm
        from noesis.llm.runtime_snapshot import set_runtime_model_snapshots
        from noesis.services.user_llm_service import UserLLMService

        # 与任务执行同链路：用户设置的默认模型优先，无则平台默认
        model_id = await UserLLMService.get_default_model(db, user_id=str(user_id))
        hint_target = "平台默认模型"
        if model_id:
            snapshots = await UserLLMService.resolve_runtime_snapshots(
                db, user_id=str(user_id), model_id=model_id
            )
            if not snapshots:
                raise ValueError("解析失败：你配置的默认模型不可用，请检查设置页的模型配置")
            set_runtime_model_snapshots(snapshots)
            hint_target = f"你配置的模型（{model_id}）"
        llm = get_llm(model_id=model_id)
        try:
            resp = await llm.ainvoke(prompt)
            content = getattr(resp, "text", "") or str(resp)
        except Exception as e:
            logger.exception("scheduled task NL parse LLM call failed")
            raise ValueError(f"解析失败，{_llm_error_hint(e, hint_target)}") from e
        data = _extract_json_object(content)
        if data is None:
            raise ValueError("解析失败，请直接编辑表单")
        name = str(data.get("name") or "").strip()[:30] or _truncate(raw, 30)
        cron_expr = str(data.get("cron_expr") or "").strip()
        prompt_text = str(data.get("prompt") or "").strip() or raw
        timezone = "Asia/Shanghai"
        validate_cron_expr(cron_expr, timezone)
        return {
            "name": name,
            "cron_expr": cron_expr,
            "timezone": timezone,
            "qa_type": IntentEnum.SUPER_AGENT_QA.value[0],
            "prompt": prompt_text,
            "session_binding": "single",
            "delivery": "none",
            "summary": cron_summary(cron_expr, timezone),
            "next_run_at": compute_next_run_ms(cron_expr, timezone),
        }

    @classmethod
    async def create_task(
        cls, db: AsyncSession, user_id: str, payload: Dict[str, Any], *, commit: bool = True
    ) -> Dict[str, Any]:
        uid = str(user_id)
        name = str(payload.get("name") or "").strip() or "未命名任务"
        cron_expr = str(payload.get("cron_expr") or "").strip()
        timezone = str(payload.get("timezone") or "Asia/Shanghai").strip()
        qa_type = str(payload.get("qa_type") or IntentEnum.SUPER_AGENT_QA.value[0])
        if qa_type not in _ALLOWED_QA:
            raise ValueError(f"不支持的 qa_type: {qa_type}")
        validate_cron_expr(cron_expr, timezone)
        session_binding = str(payload.get("session_binding") or "none")
        delivery = str(payload.get("delivery") or "none")
        # 单任务单会话：预建一个空会话并绑定，所有定时运行追加进同一线程。
        if session_binding == "single":
            session_id = await cls._provision_bound_session(db, uid, name, qa_type)
            session_binding = f"session:{session_id}"
        await cls._validate_targets(db, uid, session_binding, delivery)
        now = now_ms()
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
        user_id: str,
        task_id: str,
        payload: Dict[str, Any],
        *,
        commit: bool = True,
    ) -> Optional[Dict[str, Any]]:
        uid = str(user_id)
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
        now = now_ms()
        row.next_run_at = compute_next_run_ms(row.cron_expr, row.timezone, after_ms=now)
        row.updated_at = now
        if commit:
            await db.commit()
            await db.refresh(row)
        else:
            await db.flush()
        return _to_dict(row)

    @staticmethod
    async def delete_task(db: AsyncSession, user_id: str, task_id: str) -> bool:
        uid = str(user_id)
        result = await db.execute(update(TUserScheduledTask).where(
            and_(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == uid, TUserScheduledTask.deleted_at.is_(None))
        ).values(enabled=False, deleted_at=now_ms(), updated_at=now_ms()))
        await db.commit()
        return (result.rowcount or 0) > 0

    @classmethod
    async def set_enabled(
        cls, db: AsyncSession, user_id: str, task_id: str, enabled: bool
    ) -> Optional[Dict[str, Any]]:
        return await cls.update_task(db, user_id, task_id, {"enabled": enabled})

    @classmethod
    async def run_once(
        cls, db: AsyncSession, user_id: str, task_id: str, idempotency_key: str | None = None
    ) -> Optional[Dict[str, Any]]:
        """手动触发：只创建 queued 运行记录并后台派发执行，立即返回 run id，不阻塞 HTTP。

        真正的 agent 执行在后台 asyncio 任务里进行（独立 db session），避免 SuperAgent 深度任务
        导致 HTTP 请求超时。前端通过 run 记录状态（queued→running→succeeded/failed）追踪结果。
        """
        uid = str(user_id)
        result = await db.execute(
            select(TUserScheduledTask).where(
                and_(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == uid, TUserScheduledTask.deleted_at.is_(None))
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        idem = idempotency_key or str(uuid.uuid4())
        # 先建 queued 记录并提交，拿到 run id 立即返回；执行交给后台任务。
        run = await cls._create_run_record(db, row, trigger_source="manual", idempotency_key=idem)
        now = now_ms()
        row.last_status = run.status
        row.last_error = run.error_message
        row.last_run_at = now
        row.next_run_at = compute_next_run_ms(row.cron_expr, row.timezone, after_ms=now)
        row.updated_at = now
        await db.commit()
        await db.refresh(row)
        # 后台派发执行（独立 session，不依赖请求级 db）。
        asyncio.create_task(cls._run_in_background(row.id, uid, run.id))
        payload = _to_dict(row)
        payload["run"] = _run_to_dict(run)
        return payload

    @staticmethod
    async def _create_run_record(
        db: AsyncSession, row: TUserScheduledTask, *, trigger_source: str, idempotency_key: str, retry_of: str | None = None
    ) -> TUserScheduledTaskRun:
        """创建幂等的 queued 运行记录（不含执行），供手动触发立刻返回。"""
        existing_result = await db.execute(select(TUserScheduledTaskRun).where(TUserScheduledTaskRun.user_id == row.user_id, TUserScheduledTaskRun.idempotency_key == idempotency_key))
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            return existing
        now = now_ms()
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
        return run

    @classmethod
    async def _run_in_background(cls, task_id: str, user_id: str, run_id: str) -> None:
        """后台执行已创建的 run：加载行后委托 _execute_and_finalize。独立 db session。"""
        from noesis.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as db:
            result = await db.execute(select(TUserScheduledTask).where(
                and_(TUserScheduledTask.id == task_id, TUserScheduledTask.user_id == user_id, TUserScheduledTask.deleted_at.is_(None))
            ))
            row = result.scalar_one_or_none()
            if row is None:
                return
            run_result = await db.execute(select(TUserScheduledTaskRun).where(TUserScheduledTaskRun.id == run_id, TUserScheduledTaskRun.user_id == user_id))
            run = run_result.scalar_one_or_none()
            if run is None:
                return
            # 已进入终态的 run 不重复执行（幂等）。
            if run.status in {"succeeded", "failed", "cancelled", "interrupted"}:
                return
            await cls._execute_and_finalize(db, row, run)

    # 主 run 返回后会话交付链的完成等待：轮询间隔与总超时（工程常量）
    _DELIVERY_POLL_SECONDS = 5.0
    _DELIVERY_TIMEOUT_SECONDS = 6 * 60 * 60.0

    @classmethod
    async def _await_session_delivery(cls, user_id: str, session_id: str) -> bool:
        """等待会话交付链完成：无活跃后台任务、无待发续跑唤醒、无活跃 run。

        主 run 返回不代表交付完成——无人值守会话的真实产出在子任务与
        continuation 链里。终态判定须等交付链完成，否则 scheduled run 在
        「子任务已转后台」时就自欺为 succeeded（生产事故缺口 3）。
        会话曾有后台任务时要求连续两次空闲观测（间隔一个轮询周期），
        关闭「任务刚落终态、去抖唤醒尚未武装」的毫秒竞态。
        超时返回 False（调用方按 delivery_timeout 标记终态，防 watcher 泄漏）。
        """
        from noesis.agents.background.executor import BackgroundTaskExecutor
        from noesis.services.bg_continuation_service import has_pending_wake

        idle_needed = 1
        idle_seen = 0
        deadline = time.time() + cls._DELIVERY_TIMEOUT_SECONDS
        while time.time() < deadline:
            tasks = BackgroundTaskExecutor.list_for_session(session_id)
            if any(t.get("status") in ("queued", "running") for t in tasks):
                idle_seen = 0
            elif has_pending_wake(session_id):
                idle_seen = 0
            else:
                from noesis.repositories.agent_run_repository import AgentRunRepository
                from noesis.storage.postgres.manager import pg_manager

                async with pg_manager.get_async_session_context() as db:
                    active = await AgentRunRepository(db).get_active_for_session(user_id, session_id)
                if active is not None:
                    idle_seen = 0
                else:
                    idle_needed = 2 if tasks else 1
                    idle_seen += 1
                    if idle_seen >= idle_needed:
                        return True
            await asyncio.sleep(cls._DELIVERY_POLL_SECONDS)
        return False

    @staticmethod
    def _delivery_outcome(session_id: str) -> tuple[bool, str]:
        """交付结局：会话后台任务存在 failed/timed_out 即未完成（cancelled 是模型主动行为，不计失败）。"""
        from noesis.agents.background.executor import BackgroundTaskExecutor

        failed = [
            t for t in BackgroundTaskExecutor.list_for_session(session_id)
            if t.get("status") in ("failed", "timed_out")
        ]
        if failed:
            names = "、".join(
                str(t.get("description") or t.get("task_id"))[:40] for t in failed[:3]
            )
            return False, f"子任务未交付完成（{len(failed)} 个失败/超时：{names}）"
        return True, ""

    @staticmethod
    async def _latest_run_text(user_id: str, session_id: str) -> str:
        """会话最新 run 的 assistant 文本（continuation 链的最终交付）；取不到返回空。"""
        try:
            from noesis.chat.delivery.telegram.adapter import extract_plain_text_from_parts
            from noesis.repositories.agent_run_repository import AgentRunRepository
            from noesis.storage.postgres.manager import pg_manager

            async with pg_manager.get_async_session_context() as db:
                run = await AgentRunRepository(db).get_latest_for_session(user_id, session_id)
                snapshot = run.snapshot if isinstance(run.snapshot, dict) else {}
            return extract_plain_text_from_parts(snapshot)[:1000]
        except Exception:  # noqa: BLE001
            return ""

    @classmethod
    async def _execute_and_finalize(
        cls, db: AsyncSession, row: TUserScheduledTask, run: TUserScheduledTaskRun
    ) -> TUserScheduledTaskRun:
        """执行主体 + 等待交付链完成 + 终态判定（手动触发 / 调度 / 重试共用）。"""
        run.status = "running"
        run.started_at = now_ms()
        await db.commit()
        await db.refresh(run)
        await db.refresh(row)
        try:
            result_obj = await cls._execute_task(row)
            session_id = getattr(result_obj, "session_id", None)
            delivered = (
                await cls._await_session_delivery(str(row.user_id), session_id)
                if session_id else True
            )
            if not delivered:
                run.status = "failed"
                run.error_category = "delivery_timeout"
                run.error_message = "任务执行完成但会话交付链长时间未完成（后台任务或续跑未结束）"
            elif session_id:
                ok, reason = cls._delivery_outcome(session_id)
                if not ok:
                    run.status = "failed"
                    run.error_category = "subtask_failed"
                    run.error_message = reason
                else:
                    run.status = "succeeded"
            else:
                run.status = "succeeded"
            run.session_id = session_id
            summary = (
                await cls._latest_run_text(str(row.user_id), session_id)
                if session_id else ""
            )
            run.result_summary = (summary or str(getattr(result_obj, "plain_text", "") or ""))[:1000]
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
        run.finished_at = now_ms()
        now = now_ms()
        row.last_status = run.status
        row.last_error = run.error_message
        row.last_run_at = now
        row.updated_at = now
        await db.commit()
        await db.refresh(run)
        return run

    @staticmethod
    async def _execute_task(row: TUserScheduledTask) -> None:
        """经渠道 headless 路径 run_channel_agent 执行，不另建 Agent 调用路径。

        定时任务无人值守：注入自动化模式 prompt 前缀 + 禁用 HITL，避免 agent 卡在 ask_user/审批等待。
        用户点进定时会话手动续聊时走网页 RunService 路径，HITL 照常生效，不受此影响。
        """
        from noesis.services.channel_run_service import run_channel_agent

        session_id = row.session_binding.removeprefix("session:") if row.session_binding.startswith("session:") else str(uuid.uuid4())
        query = _AUTOMATION_MODE_PROMPT + row.prompt
        return await run_channel_agent(
            user_id=row.user_id, session_id=session_id, query=query,
            qa_type=row.qa_type, origin="automation", channel_type="automation",
            disable_hitl=True,
        )

    @staticmethod
    async def list_runs(db: AsyncSession, user_id: str, task_id: str, page: int, page_size: int) -> dict:
        uid = str(user_id)
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
    async def get_run(db: AsyncSession, user_id: str, run_id: str) -> TUserScheduledTaskRun | None:
        result = await db.execute(select(TUserScheduledTaskRun).where(TUserScheduledTaskRun.id == run_id, TUserScheduledTaskRun.user_id == str(user_id)))
        return result.scalar_one_or_none()

    @classmethod
    async def retry_run(cls, db: AsyncSession, user_id: str, run_id: str, idempotency_key: str) -> dict | None:
        old = await cls.get_run(db, user_id, run_id)
        if old is None:
            return None
        if old.status not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("只有失败、已取消或被中断的运行可以重试")
        result = await db.execute(select(TUserScheduledTask).where(TUserScheduledTask.id == old.task_id, TUserScheduledTask.user_id == str(user_id), TUserScheduledTask.deleted_at.is_(None)))
        task = result.scalar_one_or_none()
        if task is None:
            raise ValueError("任务已删除，无法重试")
        # 与手动触发同款：建 queued 记录立即返回，执行后台派发（等交付链完成才落终态）
        run = await cls._create_run_record(db, task, trigger_source="retry", idempotency_key=idempotency_key, retry_of=old.id)
        asyncio.create_task(cls._run_in_background(task.id, str(user_id), run.id))
        return _run_to_dict(run)

    @staticmethod
    async def cleanup_runs(db: AsyncSession, user_id: str, *, retention_days: int = 30, max_records: int = 1000) -> int:
        uid = str(user_id)
        cutoff = now_ms() - retention_days * 24 * 60 * 60 * 1000
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
        db: AsyncSession, user_id: str, session_id: str, *, reason: str
    ) -> int:
        uid = str(user_id)
        binding = f"session:{session_id}"
        now = now_ms()
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
    async def delete_all_for_user(db: AsyncSession, user_id: str) -> int:
        uid = str(user_id)
        result = await db.execute(
            delete(TUserScheduledTask).where(TUserScheduledTask.user_id == uid)
        )
        await db.commit()
        return int(result.rowcount or 0)

    @staticmethod
    async def reconcile_interrupted_runs(db: AsyncSession) -> int:
        """进程重启后将遗留的 queued/running 运行记录标记为 interrupted。

        调度器与手动触发的执行体都在进程内（_run_in_background await
        全程，含交付链完成等待），重启即丢失；claim_due_tasks 已推进
        next_run_at（下次触发照常），但遗留行无人标记终态——设置页永久显示
        running、任务行 last_status 卡死。终态行（succeeded/failed/
        cancelled/interrupted）不动；须在调度器启动前调用（lifespan
        leader-only 对账块）。
        """
        now = now_ms()
        result = await db.execute(
            update(TUserScheduledTaskRun)
            .where(TUserScheduledTaskRun.status.in_(["queued", "running"]))
            .values(
                status="interrupted",
                error_category="server_restart",
                error_message="后端进程重启，运行已中断",
                finished_at=now,
            )
        )
        interrupted = int(result.rowcount or 0)
        if interrupted:
            await db.execute(
                update(TUserScheduledTask)
                .where(TUserScheduledTask.last_status.in_(["queued", "running"]))
                .values(
                    last_status="interrupted",
                    last_error="后端进程重启，上次运行已中断",
                    updated_at=now,
                )
            )
        await db.commit()
        if interrupted:
            logger.warning("定时任务重启对账：{} 个遗留 run 已标记为 interrupted", interrupted)
        return interrupted

    @staticmethod
    async def claim_due_tasks(db: AsyncSession, *, limit: int = 20) -> List[TUserScheduledTask]:
        """抢占到期任务（Postgres FOR UPDATE SKIP LOCKED）。"""
        now = now_ms()
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
