import jwt
import bcrypt
from datetime import datetime, timedelta, timezone

from exceptions.exception import AuthException
from config.env import JwtConfig

SECRET_KEY = JwtConfig.jwt_secret_key
ALGORITHM = JwtConfig.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = JwtConfig.jwt_expire_minutes


class PwdUtil:
    @classmethod
    def verify_password(cls, plain_password: str, hashed_password: str) -> bool:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )

    @classmethod
    def get_password_hash(cls, password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @classmethod
    def create_access_token(cls, user_id: int, user_name: str):
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode = {"exp": expire, "id": str(user_id), "name": user_name}
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    @classmethod
    def decode_token(cls, token: str) -> dict:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            raise AuthException(data='', message='用户token不合法')
        except jwt.InvalidTokenError:
            raise AuthException(data='', message='用户token不合法')


