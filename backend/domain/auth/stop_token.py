"""流式会话短期 stop 凭据：JWT 绑定 session_id + user_id。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from config.env import JwtConfig

STOP_TOKEN_TYP = "stop"
STOP_TOKEN_HEADER = "X-Stop-Token"


class StopTokenService:
    @staticmethod
    def create(session_id: str, user_id: int) -> str:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=JwtConfig.jwt_stop_token_expire_minutes
        )
        payload = {
            "typ": STOP_TOKEN_TYP,
            "sid": session_id,
            "uid": str(user_id),
            "exp": expire,
        }
        return jwt.encode(
            payload,
            JwtConfig.jwt_secret_key,
            algorithm=JwtConfig.jwt_algorithm,
        )

    @staticmethod
    def verify(session_id: str, token: str) -> Optional[int]:
        if not token or not session_id:
            return None
        try:
            payload = jwt.decode(
                token,
                JwtConfig.jwt_secret_key,
                algorithms=[JwtConfig.jwt_algorithm],
            )
        except jwt.InvalidTokenError:
            return None
        if payload.get("typ") != STOP_TOKEN_TYP:
            return None
        if payload.get("sid") != session_id:
            return None
        uid = payload.get("uid")
        if not uid:
            return None
        try:
            return int(uid)
        except (TypeError, ValueError):
            return None
