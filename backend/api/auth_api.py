from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from common.http.response import ResponseUtil
from config.get_db import get_db
from config.env import SessionConfig
from domain.auth.session import SessionService
from domain.auth.session_cookie import attach_session_cookie, clear_session_cookie
from exceptions.exception import AuthException
from schemas.login_vo import CurrentUser, UserLogin, UserRegistrationRequest
from services.login_service import LoginService
from services.user_service import UserService

auth_router = APIRouter(prefix="/api/auth")


def _payload(user: CurrentUser, session, csrf_token: str) -> dict:
    return {"user": {"id": user.user_id, "username": user.username, "mobile": user.mobile},
            "session": {"id": session.id, "created_at": session.created_at, "last_seen_at": session.last_seen_at,
                        "idle_expires_at": session.idle_expires_at, "absolute_expires_at": session.absolute_expires_at},
            "csrf_token": csrf_token}


@auth_router.post("/login")
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = await LoginService.authenticate_user(request, db, UserLogin(username=form_data.username, password=form_data.password))
    # SessionService.create 会提交当前 AsyncSession；提交后 ORM 实体默认过期，
    # 不得再通过同步属性访问 user，否则会触发 MissingGreenlet 式隐式懒加载。
    user_id = user.id
    username = user.username or ""
    mobile = user.mobile
    issued = await SessionService.create(
        db,
        user_id,
        request.headers.get("user-agent", ""),
        request.client.host if request.client else None,
    )
    current = CurrentUser(user_id=user_id, username=username, mobile=mobile)
    response = ResponseUtil.success(msg="登录成功", data=_payload(current, issued.session, issued.csrf_token))
    attach_session_cookie(response, issued.raw_session_id, SessionConfig.idle_expire_days * 86_400)
    return response


@auth_router.post("/register")
async def register(request: Request, body: UserRegistrationRequest, db: AsyncSession = Depends(get_db)):
    user = await LoginService.register_with_invite(db, body)
    issued = await SessionService.create(
        db,
        user.id,
        request.headers.get("user-agent", ""),
        request.client.host if request.client else None,
    )
    current = CurrentUser(user_id=user.id, username=user.username or "", mobile=user.mobile)
    response = ResponseUtil.success(msg="注册成功", data=_payload(current, issued.session, issued.csrf_token))
    attach_session_cookie(response, issued.raw_session_id, SessionConfig.idle_expire_days * 86_400)
    return response


@auth_router.get("/session")
async def current_session(request: Request, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    session = request.state.auth_session
    csrf_token = await SessionService.rotate_csrf(db, session)
    response = ResponseUtil.success(data=_payload(current, session, csrf_token))
    attach_session_cookie(response, request.cookies[SessionConfig.cookie_name], SessionService.remaining_seconds(session))
    return response


@auth_router.post("/logout")
async def logout(request: Request, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.require_csrf(request)
    await SessionService.revoke(db, request.state.auth_session)
    response = ResponseUtil.success(msg="已退出登录")
    clear_session_cookie(response)
    return response


@auth_router.post("/logout-all")
async def logout_all(request: Request, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.require_csrf(request)
    await SessionService.revoke_all(db, current.user_id)
    response = ResponseUtil.success(msg="已退出全部设备")
    clear_session_cookie(response)
    return response


@auth_router.get("/sessions")
async def list_sessions(request: Request, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    sessions = await SessionService.list_active(db, current.user_id)
    return ResponseUtil.success(data={"sessions": [
        {"id": item.id, "created_at": item.created_at, "last_seen_at": item.last_seen_at,
         "idle_expires_at": item.idle_expires_at, "absolute_expires_at": item.absolute_expires_at,
         "device_name": item.device_name, "last_ip": item.last_ip,
         "current": item.id == request.state.auth_session.id}
        for item in sessions
    ]})


@auth_router.delete("/sessions/{target_session_id}")
async def revoke_session(target_session_id: str, request: Request, current: CurrentUser = Depends(UserService.get_current_user), db: AsyncSession = Depends(get_db)):
    await UserService.require_csrf(request)
    if not await SessionService.revoke_by_id(db, current.user_id, target_session_id):
        return ResponseUtil.not_found(msg="会话不存在")
    response = ResponseUtil.success(msg="会话已撤销")
    if target_session_id == request.state.auth_session.id:
        clear_session_cookie(response)
    return response
