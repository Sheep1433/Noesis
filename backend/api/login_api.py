from datetime import timedelta
from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from schemas.login_vo import Token, UserLogin
from config.get_db import get_db
from config.env import JwtConfig
from services.login_service import LoginService
from utils.log_util import logger
from utils.response_util import ResponseUtil

login_router = APIRouter(prefix="/api/user")


@login_router.post('/login', response_model=Token)
async def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = UserLogin(username=form_data.username, password=form_data.password)
    result = await LoginService.authenticate_user(request, db, user)
    access_token_expires = timedelta(minutes=JwtConfig.jwt_expire_minutes)
    access_token = await LoginService.create_access_token(
        data={
            'id': str(result.id),
            'username': result.username,
        },
        expires_delta=access_token_expires,
    )
    logger.info('登录成功')
    return ResponseUtil.success(msg='登录成功', data={'token': access_token})