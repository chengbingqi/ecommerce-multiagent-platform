"""Create or reset the restricted public demo account.

The script never accepts a password on argv and never prints credentials.
Use getpass prompts by default. For local automation only, a mode-0600 file may
be supplied through DEMO_BOOTSTRAP_PASSWORD_FILE.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db.models import AuthSession, AuthUser, Principal, Store
from app.db.session import get_sessionmaker
from app.security.demo import DEMO_ACCOUNT_TYPE
from app.security.password_policy import validate_admin_password
from app.security.service import AuthService

DEFAULT_USERNAME = "demo_user"
DEFAULT_PRINCIPAL_ID = "public-demo-001"
DEFAULT_STORE_SCOPE_KEY = "store-001"
DEMO_ALLOWED_ROLE = "store_operator"


class DemoBootstrapError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _username(value: str | None) -> str:
    prompt = f"公共演示账号用户名 [{DEFAULT_USERNAME}]："
    raw = value if value is not None else input(prompt)
    normalized = AuthService.normalize_username(raw or DEFAULT_USERNAME)
    if not normalized:
        raise RuntimeError("公共演示账号用户名不能为空")
    return normalized


def _password(label: str = "公共演示账号密码") -> str:
    secret_file = os.environ.get("DEMO_BOOTSTRAP_PASSWORD_FILE")
    if secret_file:
        path = Path(secret_file)
        if path.stat().st_mode & 0o077:
            raise RuntimeError("public demo password file permissions are too broad")
        return validate_admin_password(path.read_text(encoding="utf-8").strip())
    first = getpass.getpass(f"{label}：")
    second = getpass.getpass("再次输入密码：")
    if first != second:
        raise RuntimeError("两次输入的密码不一致")
    return validate_admin_password(first)


async def _store_id(scope_key: str) -> str:
    async with get_sessionmaker()() as session:
        store = await session.scalar(select(Store).where(Store.scope_key == scope_key))
        if store is None:
            raise RuntimeError("指定的模拟店铺不存在")
        return store.id


async def create(username: str, principal_id: str, store_scope_key: str) -> dict[str, str]:
    now = datetime.now(UTC)
    normalized = AuthService.normalize_username(username)
    try:
        async with get_sessionmaker()() as session, session.begin():
            store = await session.scalar(select(Store).where(Store.scope_key == store_scope_key))
            if store is None:
                raise DemoBootstrapError("STORE_NOT_FOUND", "指定的模拟店铺不存在")
            store_id = store.id
            existing = await session.scalar(
                select(AuthUser).where(AuthUser.username_normalized == normalized)
            )
            principal_user = await session.scalar(
                select(AuthUser).where(AuthUser.principal_id == principal_id)
            )
            if existing:
                if existing.principal_id != principal_id:
                    raise DemoBootstrapError(
                        "USERNAME_CONFLICT", "同名登录已关联其他Principal，已停止。"
                    )
                principal = await session.get(Principal, existing.principal_id)
                if principal is None:
                    raise DemoBootstrapError(
                        "AUTH_USER_ORPHANED", "演示账号存在但Principal缺失，已停止。"
                    )
                if (
                    existing.account_type != DEMO_ACCOUNT_TYPE
                    or principal.role != DEMO_ALLOWED_ROLE
                    or principal.store_id != store_id
                    or not existing.active
                    or not principal.active
                ):
                    raise DemoBootstrapError(
                        "DEMO_ACCOUNT_INVALID", "同名账号不是受限公共演示账号，已停止。"
                    )
                return {
                    "status": "EXISTS",
                    "username": normalized,
                    "principal_id": principal.id,
                    "account_type": existing.account_type,
                    "role": principal.role,
                    "store_scope": principal.store_id or "",
                    "enabled": str(existing.active and principal.active),
                }
            if principal_user:
                raise DemoBootstrapError(
                    "PRINCIPAL_CONFLICT", "公共演示Principal已关联其他登录账号，已停止。"
                )
            principal = await session.get(Principal, principal_id)
            if principal is None:
                principal = Principal(
                    id=principal_id,
                    role=DEMO_ALLOWED_ROLE,
                    store_id=store_id,
                    customer_ref=None,
                    active=True,
                )
                session.add(principal)
                await session.flush()
            elif (
                principal.role != DEMO_ALLOWED_ROLE
                or principal.store_id != store_id
                or principal.customer_ref is not None
                or not principal.active
            ):
                raise DemoBootstrapError(
                    "PRINCIPAL_INVALID",
                    "existing principal is not the restricted demo store operator",
                )
            password_hash = AuthService.hash_password(_password())
            session.add(
                AuthUser(
                    id=str(uuid.uuid4()),
                    principal_id=principal_id,
                    username_normalized=normalized,
                    password_hash=password_hash,
                    account_type=DEMO_ACCOUNT_TYPE,
                    auth_version=1,
                    failed_attempts=0,
                    locked_until=None,
                    active=True,
                    password_changed_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
    except IntegrityError as exc:
        raise DemoBootstrapError(
            "INTEGRITY_ERROR", "公共演示账号创建失败：数据库约束检查未通过，事务已回滚。"
        ) from exc
    except SQLAlchemyError as exc:
        raise DemoBootstrapError(
            "DATABASE_ERROR", "公共演示账号创建失败：数据库操作失败，事务已回滚。"
        ) from exc
    return {
        "status": "CREATED",
        "username": normalized,
        "principal_id": principal_id,
        "account_type": DEMO_ACCOUNT_TYPE,
        "role": DEMO_ALLOWED_ROLE,
        "store_scope": store_id,
        "enabled": "True",
    }


async def status(username: str, store_scope_key: str) -> dict[str, str]:
    normalized = AuthService.normalize_username(username)
    expected_store_id = await _store_id(store_scope_key)
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                select(AuthUser, Principal)
                .join(Principal, Principal.id == AuthUser.principal_id)
                .where(AuthUser.username_normalized == normalized)
            )
        ).one_or_none()
    if row is None:
        raise DemoBootstrapError("ACCOUNT_NOT_FOUND", "公共演示账号不存在。")
    user, principal = row
    if (
        user.account_type != DEMO_ACCOUNT_TYPE
        or principal.role != DEMO_ALLOWED_ROLE
        or principal.store_id != expected_store_id
        or principal.customer_ref is not None
        or not user.active
        or not principal.active
    ):
        raise DemoBootstrapError("DEMO_ACCOUNT_INVALID", "公共演示账号状态或权限范围异常。")
    return {
        "status": "VALID",
        "username": normalized,
        "principal_id": principal.id,
        "account_type": user.account_type,
        "role": principal.role,
        "store_scope": principal.store_id or "",
        "enabled": str(user.active and principal.active),
    }


async def reset_password(username: str) -> dict[str, str]:
    now = datetime.now(UTC)
    normalized = AuthService.normalize_username(username)
    password_hash = AuthService.hash_password(_password("公共演示账号新密码"))
    async with get_sessionmaker()() as session, session.begin():
        row = (
            await session.execute(
                select(AuthUser, Principal)
                .join(Principal, Principal.id == AuthUser.principal_id)
                .where(AuthUser.username_normalized == normalized)
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise RuntimeError("公共演示账号不存在")
        user, principal = row
        if user.account_type != DEMO_ACCOUNT_TYPE or principal.role != "store_operator":
            raise RuntimeError("目标账号不是受限公共演示账号")
        user.password_hash = password_hash
        user.auth_version += 1
        user.failed_attempts = 0
        user.locked_until = None
        user.password_changed_at = now
        user.updated_at = now
        await session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now, revoke_reason="public_demo_password_reset")
        )
    return {
        "status": "RESET_PASSWORD",
        "username": normalized,
        "principal_id": principal.id,
        "account_type": DEMO_ACCOUNT_TYPE,
        "role": principal.role,
        "store_scope": principal.store_id or "",
        "enabled": str(user.active and principal.active),
    }


def _print_safe(result: dict[str, str]) -> None:
    print(
        "公共演示账号："
        f"{result['username']}；"
        f"Principal ID：{result.get('principal_id', '')}；"
        f"账号类型：{result['account_type']}；"
        f"角色：{result['role']}；"
        f"store_scope：{result['store_scope']}；"
        f"启用：{result['enabled']}；"
        f"状态：{result['status']}；"
        "密码：由用户设置"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=["status", "create", "reset-password"],
        nargs="?",
        default="create",
    )
    parser.add_argument("--username")
    parser.add_argument("--principal-id", default=DEFAULT_PRINCIPAL_ID)
    parser.add_argument("--store-scope-key", default=DEFAULT_STORE_SCOPE_KEY)
    args = parser.parse_args()
    username = _username(args.username)
    try:
        if args.operation == "status":
            result = asyncio.run(status(username, args.store_scope_key))
        elif args.operation == "reset-password":
            result = asyncio.run(reset_password(username))
        else:
            result = asyncio.run(create(username, args.principal_id, args.store_scope_key))
        _print_safe(result)
    except DemoBootstrapError as exc:
        raise SystemExit(f"公共演示账号创建失败：{exc.message}（{exc.code}）") from None


if __name__ == "__main__":
    main()
