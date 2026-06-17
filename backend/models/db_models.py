from typing import Optional
import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import VARCHAR, DateTime, Integer
from config.database import Base


class TUser(Base):
    __tablename__ = "t_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[Optional[str]] = mapped_column(VARCHAR(200), comment="用户名称")
    password: Mapped[Optional[str]] = mapped_column(VARCHAR(300), comment="密码")
    mobile: Mapped[Optional[str]] = mapped_column(VARCHAR(100), comment="手机号")
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment="创建时间")
    update_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, comment="修改时间")


