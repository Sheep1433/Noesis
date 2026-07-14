from fastapi import Response

from config.env import AppConfig, SessionConfig


def is_secure_cookie() -> bool:
    # prod 环境通过 HTTP nginx 反代对外暴露，不支持 HTTPS，
    # 设置 Secure 标记会导致浏览器不发送 cookie → 表现为"登录已过期"
    return False


def attach_session_cookie(response: Response, raw_session_id: str, max_age_seconds: int) -> None:
    response.set_cookie(SessionConfig.cookie_name, raw_session_id, max_age=max_age_seconds,
                        httponly=True, secure=is_secure_cookie(), samesite="lax", path="/")


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SessionConfig.cookie_name, httponly=True, secure=is_secure_cookie(), samesite="lax", path="/")
