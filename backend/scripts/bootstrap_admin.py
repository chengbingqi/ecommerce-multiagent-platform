"""Create the initial administrator without accepting a password on argv.

Usage: pass --username for non-interactive username entry, or enter the
username at the hidden prompt. Set ADMIN_BOOTSTRAP_PASSWORD_FILE to a mode-0600
file, or enter the password at the hidden prompt. The script never prints
credentials.
"""

import argparse
import asyncio
import getpass
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.db.models import AuthUser, Principal
from app.db.session import get_sessionmaker
from app.security.password_policy import validate_admin_password
from app.security.service import AuthService


def _password() -> str:
    secret_file = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD_FILE")
    if secret_file:
        path = Path(secret_file)
        if path.stat().st_mode & 0o077:
            raise RuntimeError("administrator password file permissions are too broad")
        return validate_admin_password(path.read_text(encoding="utf-8").strip())
    first = getpass.getpass("管理员初始密码：")
    second = getpass.getpass("再次输入管理员初始密码：")
    if first != second:
        raise RuntimeError("两次输入的密码不一致")
    return validate_admin_password(first)


def _username(value: str | None) -> str:
    username = value if value is not None else getpass.getpass("管理员用户名：")
    normalized = AuthService.normalize_username(username)
    if not normalized:
        raise RuntimeError("管理员用户名不能为空")
    return normalized


async def create(username: str, principal_id: str) -> str:
    now = datetime.now(UTC)
    normalized = AuthService.normalize_username(username)
    password_hash = AuthService.hash_password(_password())
    async with get_sessionmaker()() as session, session.begin():
        existing = await session.scalar(
            select(AuthUser).where(AuthUser.username_normalized == normalized)
        )
        if existing:
            principal = await session.get(Principal, existing.principal_id)
            if existing.active and principal and principal.active and principal.role == "admin":
                return "EXISTS_ACTIVE"
            raise RuntimeError("同名登录已存在，但不是可用的管理员身份")
        principal = await session.get(Principal, principal_id)
        if principal is None:
            principal = Principal(
                id=principal_id,
                role="admin",
                store_id=None,
                customer_ref=None,
                active=True,
            )
            session.add(principal)
        elif principal.role != "admin" or not principal.active:
            raise RuntimeError("existing principal is not an active administrator")
        session.add(
            AuthUser(
                id=str(uuid.uuid4()),
                principal_id=principal_id,
                username_normalized=normalized,
                password_hash=password_hash,
                auth_version=1,
                failed_attempts=0,
                locked_until=None,
                active=True,
                password_changed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    return "CREATED"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username")
    parser.add_argument("--principal-id", default="admin-001")
    args = parser.parse_args()
    username = _username(args.username)
    result = asyncio.run(create(username, args.principal_id))
    print(f"管理员账号：{username}；角色：admin；状态：{result}")


if __name__ == "__main__":
    main()
