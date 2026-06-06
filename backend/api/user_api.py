import math

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from config.get_db import get_db
from schemas.login_vo import CurrentUser
from schemas.qa_vo import QueryUserRecordRequest
from services.chat_service import ChatService
from services.user_service import UserService
from utils.response_util import ResponseUtil

user_router = APIRouter(prefix="/api/user")


@user_router.post("/query_user_record", summary="查询用户记录")
async def query_user_qa_record(
    request: QueryUserRecordRequest,
    current_user: CurrentUser = Depends(UserService.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    查询用户的会话列表：支持按标题模糊搜索（search_text）、按 chat_id 过滤、分页。
    """
    sessions, total = await ChatService.query_user_sessions_for_record(
        user_id=str(current_user.user_id),
        db=db,
        search_text=request.search_text,
        session_id=request.chat_id or request.session_id,
        page=request.page,
        limit=request.limit,
    )

    resolved_qa = await ChatService.resolve_session_qa_types_for_list(sessions, db)

    session_list = [
        {
            "id": s.id,
            "session_id": s.id,
            "title": s.title,
            "qa_type": resolved_qa.get(s.id),
            "create_time": s.created_at,
            "update_time": s.updated_at,
        }
        for s in sessions
    ]

    lim = min(max(request.limit, 1), 1_000_000)
    total_pages = math.ceil(total / lim) if total and lim else 0

    return ResponseUtil.success(
        msg="success",
        data={
            "records": session_list,
            "total_count": total,
            "total_pages": total_pages,
        },
    )
