"""轮换由管理员用户持有的 6 位全局注册码。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.database import AsyncSessionLocal
from domain.auth.registration_invite import RegistrationInviteService


async def _rotate(admin_username: str) -> None:
    async with AsyncSessionLocal() as db:
        code = await RegistrationInviteService.rotate(db, admin_username)
    print(f"当前邀请码已更新：{code}")
    print("旧邀请码已立即失效；数据库不会保存新邀请码明文。")


def main() -> None:
    parser = argparse.ArgumentParser(description="轮换 Noesis 全局注册码")
    parser.add_argument("--admin-username", default="admin", help="持有邀请码的管理员用户名，默认 admin")
    args = parser.parse_args()
    asyncio.run(_rotate(args.admin_username))


if __name__ == "__main__":
    main()
