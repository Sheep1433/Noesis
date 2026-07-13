from fastapi import Response

from config.env import AppConfig, SessionConfig


def is_secure_cookie() -> bool:
    return AppConfig.app_env.lower() in ("prod", "production")


def attach_session_cookie(response: Response, raw_session_id: str, max_age_seconds: int) -> None:
    response.set_cookie(SessionConfig.cookie_name, raw_session_id, max_age=max_age_seconds,
                        httponly=True, secure=is_secure_cookie(), samesite="lax", path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SessionConfig.cookie_name, httponly=True, secure=is_secure_cookie(), samesite="lax", path="/")
