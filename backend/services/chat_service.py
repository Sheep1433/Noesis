"""
Chat Service (v2.1)

聊天历史服务，实现 v2.1 架构的核心逻辑：
1. multipart 消息格式写入
2. partial 消息自动清理（流式中断时清理半成品）
3. 会话层级支持（parent_id）
4. 软删机制（会话与消息 deleted_at）
"""

import asyncio
import json
import uuid
import time
import threading
from typing import Optional, List, Dict, Any
from sqlalchemy import select, and_, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.chat_models import TChatSession, TChatMessage
from exceptions.exception import ServiceException
from config.agent_workspace_paths import delete_session_workspace
from services.agent_lifecycle import cancel_session_agent_runs
from common.logging import logger
from domain.chat.message_builder import AssistantMessageBuilder

# ============================================================================
# 加载锁：服务启动完成 PostgreSQL 检查点恢复前，业务写入须等待
# ============================================================================
_load_lock = threading.Lock()
_load_complete = False


def is_load_complete() -> bool:
    """检查是否加载完成"""
    return _load_complete


def set_load_complete() -> None:
    """标记加载完成"""
    global _load_complete
    with _load_lock:
        _load_complete = True


def wait_for_load() -> None:
    """等待加载完成（如果尚未完成）"""
    if _load_complete:
        return
    with _load_lock:
        pass  # 等待锁释放时加载应该已经完成


def _now_ms() -> int:
    """返回当前时间戳（毫秒）"""
    return int(time.time() * 1000)


# ============================================================================
# Chat Service
# ============================================================================

_DEFAULT_SESSION_TITLE = "新对话"


class ChatService:

    @classmethod
    def _normalize_session_title(cls, title: Optional[str]) -> Optional[str]:
        if not title:
            return None
        normalized = title[:100].replace("\n", " ").strip()
        return normalized or None

    @classmethod
    async def set_session_title_if_default(
            cls,
            session_id: str,
            user_id: str,
            title: str,
            db: AsyncSession = None,
    ) -> Optional[TChatSession]:
        """
        仅在会话仍为默认标题时写入标题（首条用户消息），已有自定义标题则不再更新。
        """
        normalized = cls._normalize_session_title(title)
        if not normalized:
            return None

        result = await db.execute(
            select(TChatSession).where(
                and_(
                    TChatSession.id == session_id,
                    TChatSession.user_id == user_id,
                    TChatSession.deleted_at.is_(None),
                )
            )
        )
        session_obj = result.scalar_one_or_none()
        if not session_obj:
            return None

        current = (session_obj.title or "").strip()
        if current and current != _DEFAULT_SESSION_TITLE:
            return session_obj

        return await cls.update_session_title(
            session_id=session_id,
            user_id=user_id,
            title=normalized,
            db=db,
        )

    @classmethod
    async def get_or_create_session(
            cls,
            user_id: str,
            session_id: str,
            title: Optional[str] = None,
            parent_id: Optional[str] = None,
            extra: Optional[Dict[str, Any]] = None,
            db: AsyncSession = None
    ) -> TChatSession:
        """
        获取或创建会话（根据 session_id）

        :param user_id: 用户 ID
        :param session_id: 会话 ID（前端传入，复用已有 session_id）
        :param title: 会话标题
        :param parent_id: 父会话 ID（subagent 场景）
        :param extra: 会话元数据
        :param db: 数据库会话
        :return: 会话对象
        """
        wait_for_load()

        result = await db.execute(
            select(TChatSession).where(
                and_(
                    TChatSession.id == session_id,
                    TChatSession.user_id == user_id,
                    TChatSession.deleted_at.is_(None)  # 过滤已删除
                )
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            # 历史会话 extra 未带 qa_type 时，用本次请求的 qa_type 补写到会话上，便于列表接口展示
            if extra and extra.get("qa_type"):
                cur_extra = existing.extra if isinstance(existing.extra, dict) else {}
                if not cur_extra.get("qa_type"):
                    merged = dict(cur_extra)
                    merged["qa_type"] = extra["qa_type"]
                    existing.extra = merged
                    existing.updated_at = _now_ms()
                    await db.commit()
                    await db.refresh(existing)
            if title:
                updated = await cls.set_session_title_if_default(
                    session_id=session_id,
                    user_id=user_id,
                    title=title,
                    db=db,
                )
                if updated is not None:
                    existing = updated
            return existing

        # 创建新会话
        now = _now_ms()
        session = TChatSession(
            id=session_id,
            parent_id=parent_id,
            user_id=user_id,
            title=cls._normalize_session_title(title) or _DEFAULT_SESSION_TITLE,
            extra=extra,
            created_at=now,
            updated_at=now
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        logger.info(f'创建会话成功: session_id={session_id}, user_id={user_id}, parent_id={parent_id}')
        return session

    @classmethod
    async def create_session(
            cls,
            user_id: str,
            title: Optional[str] = None,
            parent_id: Optional[str] = None,
            extra: Optional[Dict[str, Any]] = None,
            db: AsyncSession = None
    ) -> TChatSession:
        """
        创建新会话（生成新的 session_id）

        :param user_id: 用户 ID
        :param title: 会话标题
        :param parent_id: 父会话 ID（subagent 场景）
        :param extra: 会话元数据
        :param db: 数据库会话
        :return: 创建的会话对象
        """
        wait_for_load()

        if parent_id:
            parent = await cls.get_session_by_id(parent_id, user_id=user_id, db=db)
            if not parent:
                raise ServiceException(message='父会话不存在')

        session_id = str(uuid.uuid4())
        now = _now_ms()
        session = TChatSession(
            id=session_id,
            parent_id=parent_id,
            user_id=user_id,
            title=cls._normalize_session_title(title) or _DEFAULT_SESSION_TITLE,
            extra=extra,
            created_at=now,
            updated_at=now
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        logger.info(f'创建会话成功: session_id={session_id}, user_id={user_id}, parent_id={parent_id}')
        return session

    @classmethod
    async def save_message(
            cls,
            session_id: str,
            user_id: str,
            role: str,
            content: Any,
            extra: Optional[Dict[str, Any]] = None,
            parent_id: Optional[str] = None,
            status: str = 'completed',
            message_id: Optional[str] = None,
            db: AsyncSession = None
    ) -> TChatMessage:
        """
        保存消息（v2.1 multipart 格式）

        :param session_id: 会话 ID
        :param user_id: 用户 ID（消息表冗余存储，便于按用户查询与校验）
        :param role: 角色: user | assistant
        :param content: 消息内容（v2.1 multipart JSON，可以是 str 或 dict）
        :param extra: 元数据（model, tokens, finish_reason, error）
        :param parent_id: 父消息 ID
        :param status: 状态: completed | partial | error | streaming（与聊天记录 PRD 一致）
        :param message_id: 指定主键 UUID（流式 assistant 与 SSE assistant_message_id 对齐）；默认自动生成
        :param db: 数据库会话
        :return: 创建的消息对象
        """
        wait_for_load()

        message_id = message_id or str(uuid.uuid4())
        now = _now_ms()

        # 处理 content 格式：统一为 multipart JSON 格式
        if isinstance(content, str):
            # 字符串：尝试解析为 JSON dict
            try:
                content_json = json.loads(content)
            except json.JSONDecodeError:
                # 非 JSON 字符串，包装为 text part
                builder = AssistantMessageBuilder()
                builder.append_text(content)
                content_json = builder.to_dict()
        elif isinstance(content, dict):
            # dict：直接使用
            content_json = content
        else:
            content_json = {"parts": []}

        message = TChatMessage(
            id=message_id,
            session_id=session_id,
            parent_id=parent_id,
            user_id=user_id,
            role=role,
            content=content_json,
            extra=extra,
            status=status,
            created_at=now
        )

        # 添加到 session
        db.add(message)

        # 先提交消息（确保消息被保存，即使后续操作失败）
        try:
            await db.commit()
        except asyncio.CancelledError:
            logger.info(
                f"save_message commit 被取消 session_id={session_id} message_id={message_id} role={role}"
            )
            raise
        except Exception as e:
            logger.error(f'保存消息失败: {e}')
            try:
                await db.rollback()
            except Exception:
                pass
            raise

        # 更新会话的 updated_at 时间
        try:
            await db.execute(
                update(TChatSession)
                .where(TChatSession.id == session_id)
                .values(updated_at=now)
            )
            await db.commit()
        except asyncio.CancelledError:
            logger.info(
                f"save_message 更新会话时间 commit 被取消 session_id={session_id} message_id={message_id}"
            )
            raise
        except Exception as e:
            logger.warning(f'更新会话时间失败: {e}')
            try:
                await db.rollback()
            except Exception:
                pass

        # refresh 失败不影响
        try:
            await db.refresh(message)
        except Exception as e:
            logger.warning(f'刷新消息失败: {e}')

        logger.info(f'保存消息成功: message_id={message_id}, session_id={session_id}, role={role}')
        return message

    @classmethod
    async def update_assistant_message(
            cls,
            message_id: str,
            session_id: str,
            user_id: str,
            content: Any,
            status: str,
            extra: Optional[Dict[str, Any]] = None,
            db: AsyncSession = None,
    ) -> bool:
        """
        按主键更新 assistant 消息的 content / status / extra（用于流式骨架 + 增量落库）。
        """
        wait_for_load()
        now = _now_ms()

        result = await db.execute(
            select(TChatMessage).where(
                and_(
                    TChatMessage.id == message_id,
                    TChatMessage.session_id == session_id,
                    TChatMessage.user_id == user_id,
                    TChatMessage.role == "assistant",
                    TChatMessage.deleted_at.is_(None),
                )
            )
        )
        msg = result.scalar_one_or_none()
        if not msg:
            return False

        if isinstance(content, str):
            try:
                content_json = json.loads(content)
            except json.JSONDecodeError:
                builder = AssistantMessageBuilder()
                builder.append_text(content)
                content_json = builder.to_dict()
        elif isinstance(content, dict):
            content_json = content
        else:
            content_json = {"parts": []}

        old_extra = msg.extra if isinstance(msg.extra, dict) else {}
        merged_extra = {**old_extra, **(extra or {})}

        await db.execute(
            update(TChatMessage)
            .where(TChatMessage.id == message_id)
            .values(content=content_json, status=status, extra=merged_extra)
        )
        try:
            await db.commit()
        except asyncio.CancelledError:
            logger.info(
                f"update_assistant_message commit 被取消 session_id={session_id} message_id={message_id} status={status}"
            )
            raise
        except Exception as e:
            logger.error(f"更新 assistant 消息失败: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            raise

        try:
            await db.execute(
                update(TChatSession)
                .where(TChatSession.id == session_id)
                .values(updated_at=now)
            )
            await db.commit()
        except asyncio.CancelledError:
            logger.info(
                f"update_assistant_message 会话时间 commit 被取消 session_id={session_id} message_id={message_id}"
            )
            raise
        except Exception as e:
            logger.warning(f'更新会话时间失败: {e}')
            try:
                await db.rollback()
            except Exception:
                pass

        logger.info(f'更新 assistant 消息成功: message_id={message_id}, session_id={session_id}, status={status}')
        return True

    @classmethod
    async def get_session_messages(
            cls,
            session_id: str,
            db: AsyncSession = None,
            limit: int = 100,
            before_id: Optional[str] = None
    ) -> List[TChatMessage]:
        """
        获取会话消息历史（按 created_at 排序，支持分页）

        :param session_id: 会话 ID
        :param db: 数据库会话
        :param limit: 返回消息数量上限（默认100）
        :param before_id: cursor 分页：返回该消息之前的消息（不包含该消息）
        :return: 消息列表
        """
        conditions = [
            TChatMessage.session_id == session_id,
            TChatMessage.deleted_at.is_(None)  # 过滤已删除
        ]

        # cursor 分页：获取 before_id 消息的 created_at，只返回更早的消息
        if before_id:
            result = await db.execute(
                select(TChatMessage).where(TChatMessage.id == before_id)
            )
            cursor_msg = result.scalar_one_or_none()
            if cursor_msg:
                conditions.append(TChatMessage.created_at < cursor_msg.created_at)

        base_filter = and_(*conditions)
        result = await db.execute(
            select(TChatMessage)
            .where(base_filter)
            .order_by(TChatMessage.created_at.desc())  # 降序获取最老的 N 条
            .limit(limit)
        )
        # 保持升序返回
        messages = list(reversed(result.scalars().all()))
        logger.info(f'获取会话消息: session_id={session_id}, count={len(messages)}, limit={limit}, before_id={before_id}')
        return messages

    @classmethod
    async def delete_session(cls, session_id: str, user_id: str, db: AsyncSession = None) -> bool:
        """
        删除会话（软删：写入 deleted_at，并级联软删消息）

        :param session_id: 会话 ID
        :param user_id: 用户 ID（用于校验）
        :param db: 数据库会话
        :return: 是否删除成功
        """
        result = await db.execute(
            select(TChatSession).where(
                and_(
                    TChatSession.id == session_id,
                    TChatSession.user_id == user_id,
                    TChatSession.deleted_at.is_(None)
                )
            )
        )
        session_obj = result.scalar_one_or_none()
        if not session_obj:
            raise ServiceException(message='会话不存在')

        await cancel_session_agent_runs(session_id)

        # 软删：更新 deleted_at（cascade 软删消息）
        now = _now_ms()
        await db.execute(
            update(TChatSession)
            .where(TChatSession.id == session_id)
            .values(deleted_at=now)
        )
        # 级联软删该会话下的所有消息
        await db.execute(
            update(TChatMessage)
            .where(TChatMessage.session_id == session_id)
            .values(deleted_at=now)
        )
        await db.commit()
        delete_session_workspace(user_id, session_id)
        logger.info(f'软删会话成功: session_id={session_id}, user_id={user_id}（已级联软删消息）')
        return True

    @classmethod
    async def batch_delete_sessions(
            cls,
            session_ids: List[str],
            user_id: str,
            db: AsyncSession,
    ) -> int:
        """
        批量软删会话：去重后一次事务落库，跳过不存在/已删/非本人会话。

        :return: 实际删除的会话数量
        """
        uid = str(user_id)
        normalized: List[str] = []
        seen: set[str] = set()
        for raw in session_ids:
            sid = (raw or "").strip()
            if not sid or sid in seen:
                continue
            seen.add(sid)
            normalized.append(sid)

        if not normalized:
            raise ServiceException(message='请选择要删除的会话')

        result = await db.execute(
            select(TChatSession).where(
                and_(
                    TChatSession.id.in_(normalized),
                    TChatSession.user_id == uid,
                    TChatSession.deleted_at.is_(None),
                )
            )
        )
        sessions = list(result.scalars().all())
        if not sessions:
            raise ServiceException(message='会话不存在')

        missing = set(normalized) - {s.id for s in sessions}
        if missing:
            logger.warning(
                f'batch_delete_sessions 跳过无效或已删会话 user_id={uid} session_ids={sorted(missing)}'
            )

        found_ids = [s.id for s in sessions]
        for sid in found_ids:
            await cancel_session_agent_runs(sid)

        now = _now_ms()
        await db.execute(
            update(TChatSession)
            .where(TChatSession.id.in_(found_ids))
            .values(deleted_at=now)
        )
        await db.execute(
            update(TChatMessage)
            .where(TChatMessage.session_id.in_(found_ids))
            .values(deleted_at=now)
        )
        await db.commit()

        for sid in found_ids:
            delete_session_workspace(uid, sid)

        logger.info(
            f'批量软删会话成功: user_id={uid}, count={len(found_ids)}, session_ids={found_ids}'
        )
        return len(found_ids)

    @classmethod
    async def update_session_title(
            cls,
            session_id: str,
            user_id: str,
            title: str,
            db: AsyncSession = None
    ) -> TChatSession:
        """
        更新会话标题

        :param session_id: 会话 ID
        :param user_id: 用户 ID（用于校验）
        :param title: 新标题
        :param db: 数据库会话
        :return: 更新后的会话对象
        """
        result = await db.execute(
            select(TChatSession).where(
                and_(
                    TChatSession.id == session_id,
                    TChatSession.user_id == user_id,
                    TChatSession.deleted_at.is_(None)
                )
            )
        )
        session_obj = result.scalar_one_or_none()
        if not session_obj:
            raise ServiceException(message='会话不存在')

        now = _now_ms()
        await db.execute(
            update(TChatSession)
            .where(TChatSession.id == session_id)
            .values(title=title[:255] if title else '新对话', updated_at=now)
        )
        await db.commit()
        await db.refresh(session_obj)
        logger.info(f'更新会话标题: session_id={session_id}, title={title}')
        return session_obj

    @classmethod
    async def merge_session_extra(
            cls,
            session_id: str,
            user_id: str,
            patch: Dict[str, Any],
            db: AsyncSession = None,
    ) -> None:
        """浅合并会话 extra，不覆盖未出现在 patch 中的顶层键。"""
        if not patch:
            return
        result = await db.execute(
            select(TChatSession).where(
                and_(
                    TChatSession.id == session_id,
                    TChatSession.user_id == user_id,
                    TChatSession.deleted_at.is_(None),
                )
            )
        )
        session_obj = result.scalar_one_or_none()
        if not session_obj:
            return

        extra: Dict[str, Any] = dict(session_obj.extra or {})
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(extra.get(key), dict):
                merged = dict(extra[key])
                merged.update(value)
                extra[key] = merged
            else:
                extra[key] = value

        now = _now_ms()
        await db.execute(
            update(TChatSession)
            .where(TChatSession.id == session_id)
            .values(extra=extra, updated_at=now)
        )
        await db.commit()

    @classmethod
    async def get_user_sessions(
            cls,
            user_id: str,
            status: Optional[str] = None,
            db: AsyncSession = None
    ) -> List[TChatSession]:
        """
        获取用户的所有会话（过滤已删除，按 updated_at 降序）

        :param user_id: 用户 ID
        :param status: 会话状态过滤（已删除的不返回）
        :param db: 数据库会话
        :return: 会话列表
        """
        conditions = [
            TChatSession.user_id == user_id,
            TChatSession.deleted_at.is_(None)
        ]

        base_filter = and_(*conditions) if conditions else None
        result = await db.execute(
            select(TChatSession)
            .where(base_filter)
            .order_by(TChatSession.updated_at.desc())
        )
        sessions = result.scalars().all()
        logger.info(f'获取用户会话列表: user_id={user_id}, count={len(sessions)}')
        return list(sessions)

    @classmethod
    async def query_user_sessions_for_record(
        cls,
        user_id: str,
        db: AsyncSession,
        *,
        search_text: Optional[str] = None,
        session_id: Optional[str] = None,
        page: int = 1,
        limit: int = 10,
    ) -> tuple[List[TChatSession], int]:
        """
        用户「聊天记录」列表：支持按标题模糊搜索、按会话 id 精确过滤、分页。
        """
        conditions = [
            TChatSession.user_id == user_id,
            TChatSession.deleted_at.is_(None),
        ]
        if session_id:
            conditions.append(TChatSession.id == session_id)
        q = (search_text or "").strip()
        if q:
            esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(TChatSession.title.like(f"%{esc}%", escape="\\"))

        base_filter = and_(*conditions)

        cnt_result = await db.execute(
            select(func.count()).select_from(TChatSession).where(base_filter)
        )
        total = int(cnt_result.scalar_one())

        safe_limit = min(max(limit, 1), 1_000_000)
        safe_page = max(page, 1)
        offset = (safe_page - 1) * safe_limit

        result = await db.execute(
            select(TChatSession)
            .where(base_filter)
            .order_by(TChatSession.updated_at.desc())
            .offset(offset)
            .limit(safe_limit)
        )
        sessions = list(result.scalars().all())
        logger.info(
            f"query_user_sessions_for_record: user_id={user_id}, total={total}, "
            f"page={safe_page}, limit={safe_limit}, has_search={bool(q)}"
        )
        return sessions, total

    @classmethod
    async def batch_first_user_message_qa_types(
            cls,
            session_ids: List[str],
            db: AsyncSession,
    ) -> Dict[str, Optional[str]]:
        """
        按会话取最早一条 user 消息的 extra.qa_type（用于会话行未写入 qa_type 时的列表回填）。
        """
        if not session_ids:
            return {}
        result = await db.execute(
            select(TChatMessage.session_id, TChatMessage.extra, TChatMessage.created_at)
            .where(
                and_(
                    TChatMessage.session_id.in_(session_ids),
                    TChatMessage.role == "user",
                )
            )
            .order_by(TChatMessage.created_at.asc())
        )
        out: Dict[str, Optional[str]] = {}
        for row in result.all():
            sid = row.session_id
            if sid in out:
                continue
            ex = row.extra
            qt = None
            if isinstance(ex, dict):
                qt = ex.get("qa_type")
            out[sid] = qt
        return out

    @classmethod
    async def resolve_session_qa_types_for_list(
            cls,
            sessions: List[TChatSession],
            db: AsyncSession,
    ) -> Dict[str, str]:
        """会话 id -> qa_type：优先会话 extra，否则首条 user 消息，否则 COMMON_QA。"""
        from constants.code_enum import IntentEnum

        default_qt = IntentEnum.COMMON_QA.value[0]
        need_ids = [
            s.id for s in sessions
            if not (s.extra if isinstance(s.extra, dict) else {}).get("qa_type")
        ]
        first_map = await cls.batch_first_user_message_qa_types(need_ids, db)
        resolved: Dict[str, str] = {}
        for s in sessions:
            ex = s.extra if isinstance(s.extra, dict) else {}
            qt = ex.get("qa_type") or first_map.get(s.id) or default_qt
            resolved[s.id] = qt if isinstance(qt, str) and qt else default_qt
        return resolved

    @classmethod
    async def get_session_by_id(
            cls,
            session_id: str,
            user_id: Optional[str] = None,
            db: AsyncSession = None
    ) -> Optional[TChatSession]:
        """
        获取会话详情

        :param session_id: 会话 ID
        :param user_id: 用户 ID（可选，用于校验）
        :param db: 数据库会话
        :return: 会话对象
        """
        # 聊天表以字符串保存用户标识；认证层当前使用整数主键。
        # 在服务边界统一转换，避免 PostgreSQL 对 VARCHAR = INTEGER 的严格类型检查失败。
        if user_id is not None:
            user_id = str(user_id)

        conditions = [
            TChatSession.id == session_id,
            TChatSession.deleted_at.is_(None)
        ]
        if user_id:
            conditions.append(TChatSession.user_id == user_id)

        result = await db.execute(
            select(TChatSession).where(and_(*conditions))
        )
        return result.scalar_one_or_none()

    @classmethod
    async def is_session_owned_by_other(
            cls,
            session_id: str,
            user_id: str,
            db: AsyncSession,
    ) -> bool:
        """
        会话是否已存在且属于其他用户（写操作前置校验；不存在则返回 False）。
        """
        result = await db.execute(
            select(TChatSession.user_id).where(
                and_(
                    TChatSession.id == session_id,
                    TChatSession.deleted_at.is_(None),
                )
            )
        )
        owner_id = result.scalar_one_or_none()
        if owner_id is None:
            return False
        return owner_id != user_id

    @classmethod
    async def get_child_sessions(
            cls,
            parent_id: str,
            db: AsyncSession = None
    ) -> List[TChatSession]:
        """
        获取子会话列表（subagent 场景）

        :param parent_id: 父会话 ID
        :param db: 数据库会话
        :return: 子会话列表
        """
        result = await db.execute(
            select(TChatSession).where(
                and_(
                    TChatSession.parent_id == parent_id,
                    TChatSession.deleted_at.is_(None)
                )
            ).order_by(TChatSession.created_at.desc())
        )
        sessions = result.scalars().all()
        logger.info(f'获取子会话列表: parent_id={parent_id}, count={len(sessions)}')
        return list(sessions)

    @classmethod
    async def load_session_from_db(
            cls,
            session_id: str,
            db: AsyncSession = None
    ) -> Optional[Dict[str, Any]]:
        """
        从数据库加载会话及其消息

        :param session_id: 会话 ID
        :param db: 数据库会话
        :return: 包含 session 和 messages 的字典
        """
        session = await cls.get_session_by_id(session_id, db=db)
        if not session:
            return None

        messages = await cls.get_session_messages(session_id, db)

        return {
            'session': session,
            'messages': messages
        }
