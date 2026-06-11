"""访问令牌签发、HttpOnly Cookie 与滑动续期响应头。"""

from __future__ import annotations

from fastapi import Response

from config.env import AppConfig, JwtConfig
from utils.pwd_util import PwdUtil

AUTH_COOKIE_NAME = "noesis_access_token"
REFRESH_TOKEN_HEADER = "X-Refresh-Token"


class AuthTokenService:
    @staticmethod
    def issue_access_token(user_id: int, username: str) -> str:
        return PwdUtil.create_access_token(user_id=user_id, user_name=username)

    @staticmethod
    def cookie_secure() -> bool:
        """生产环境建议 HTTPS；本地 dev 为 False。"""
        return AppConfig.app_env.lower() in ("prod", "production")

    @staticmethod
    def attach_auth_cookie(response: Response, token: str) -> None:
        max_age = int(JwtConfig.jwt_expire_minutes) * 60
        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value=token,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=max_age,
            secure=AuthTokenService.cookie_secure(),
        )

    @staticmethod
    def clear_auth_cookie(response: Response) -> None:
        response.delete_cookie(
            key=AUTH_COOKIE_NAME,
            path="/",
            httponly=True,
            samesite="lax",
            secure=AuthTokenService.cookie_secure(),
        )

    @staticmethod
    def attach_sliding_refresh(response: Response, user_id: int, username: str) -> None:
        """活动续期：在成功响应上附带新 JWT（Header + Cookie）。"""
        token = AuthTokenService.issue_access_token(user_id, username)
        response.headers[REFRESH_TOKEN_HEADER] = token
        AuthTokenService.attach_auth_cookie(response, token)

    @staticmethod
    def decode_access_token(token: str) -> dict:
        return PwdUtil.decode_token(token)
