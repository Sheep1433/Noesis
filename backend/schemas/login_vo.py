from typing import Optional

from pydantic import BaseModel, Field

class UserLogin(BaseModel):
    username: str = Field(description='用户名称')
    password: str = Field(description='用户密码')


class Token(BaseModel):
    access_token: str = Field(description='token信息')
    token_type: str = Field(description='token类型')


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CurrentUser(BaseModel):
    user_id: int
    username: str
    mobile: Optional[str] = None
