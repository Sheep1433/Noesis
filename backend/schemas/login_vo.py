from typing import Optional

from pydantic import BaseModel, Field

class UserLogin(BaseModel):
    username: str = Field(description='用户名称')
    password: str = Field(description='用户密码')


class UserRegister(BaseModel):
    username: str = Field(min_length=3, max_length=50, description='用户名称')
    password: str = Field(min_length=6, max_length=32, description='用户密码')
    mobile: Optional[str] = Field(default=None, max_length=20, description='手机号')


class UserRegistrationRequest(UserRegister):
    invite_code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$", description="6 位数字邀请码")


class CurrentUser(BaseModel):
    user_id: int
    username: str
    mobile: Optional[str] = None
