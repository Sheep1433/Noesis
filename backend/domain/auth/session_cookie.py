from fastapi import Request, Response

from config.env import SessionConfig


def _is_secure(request: Request) -> bool:
    """动态检测当前请求是否来自 HTTPS。

    优先检查反向代理设置的 X-Forwarded-Proto（nginx 需配置
    proxy_set_header X-Forwarded-Proto $scheme;），
    兜底检查请求原始 scheme。
    """
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    if forwarded_proto == "https":
        return True
    return request.url.scheme == "https"


def attach_session_cookie(request: Request, response: Response, raw_session_id: str, max_age_seconds: int) -> None:
    response.set_cookie(SessionConfig.cookie_name, raw_session_id, max_age=max_age_seconds,
                        httponly=True, secure=_is_secure(request), samesite="lax", path="/")


def clear_session_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(SessionConfig.cookie_name, httponly=True, secure=_is_secure(request), samesite="lax", path="/")
