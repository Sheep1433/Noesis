"""业务领域异常（service 层抛出，api 层 catch 映射 HTTP）。

从 server/exceptions/exception.py 迁入。service 不碰 HTTPException，
只抛这些 domain exception；api 层统一 catch 映射 HTTP code。
"""
from __future__ import annotations


class LoginException(Exception):
    """登录异常。"""

    def __init__(self, data: str = None, message: str = None):
        self.data = data
        self.message = message


class ConflictException(Exception):
    """资源冲突（如用户名已存在）。"""

    def __init__(self, data: str = None, message: str = None):
        self.data = data
        self.message = message


class NotFoundException(Exception):
    """资源不存在。"""

    def __init__(self, data: str = None, message: str = None):
        self.data = data
        self.message = message


class AuthException(Exception):
    """令牌/认证异常。"""

    def __init__(self, data: str = None, message: str = None):
        self.data = data
        self.message = message


class PermissionException(Exception):
    """权限异常。"""

    def __init__(self, data: str = None, message: str = None):
        self.data = data
        self.message = message


class ServiceException(Exception):
    """服务异常（通用 500）。"""

    def __init__(self, data: str = None, message: str = None):
        self.data = data
        self.message = message


class ServiceWarning(Exception):
    """服务警告。"""

    def __init__(self, data: str = None, message: str = None):
        self.data = data
        self.message = message


class ModelValidatorException(Exception):
    """模型校验异常。"""

    def __init__(self, data: str = None, message: str = None):
        self.data = data
        self.message = message
