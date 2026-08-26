from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from noesis.errors.exceptions import AuthException, LoginException
from noesis.auth.entities import AuthUser
from noesis.repositories.auth_repository import SqlAlchemyUserRepository
from noesis.schemas.login_vo import CurrentUser
from noesis.runtime.logging import logger
from noesis.auth.password import PwdUtil


class UserService:
    @classmethod
    async def authenticate_user(cls, username: str, password: str, db: AsyncSession) -> Optional[AuthUser]:
        user = await SqlAlchemyUserRepository(db).get_by_username(username)
        if not user:
            logger.warning('用户不存在')
            raise LoginException(data='', message='用户不存在')
        if not PwdUtil.verify_password(password, user.password_hash):
            logger.warning('用户名或密码错误')
            raise LoginException(data='', message='用户名或密码错误')
        return user

    @classmethod
    async def get_user_by_id(cls, user_id: str, db: AsyncSession) -> CurrentUser:
        user = await SqlAlchemyUserRepository(db).get_by_id(user_id)
        if not user:
            raise AuthException(data='', message='用户不存在')
        return CurrentUser(user_id=user.id, username=user.username, mobile=user.mobile)

    @classmethod
    async def delete_user(cls, user_id: str, db: AsyncSession) -> bool:
        deleted = await SqlAlchemyUserRepository(db).delete(str(user_id))
        await db.commit()
        if deleted:
            UserService._delete_user_files(str(user_id))
        return deleted

    @staticmethod
    def _delete_user_files(user_id: str) -> None:
        """删除用户数据目录（含 memory/ 记忆文件），幂等。"""
        import shutil

        from noesis.config.user_data_paths import get_user_root

        root = get_user_root(user_id)
        if root.is_dir():
            shutil.rmtree(root, ignore_errors=True)
