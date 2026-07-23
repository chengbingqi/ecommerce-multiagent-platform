from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type
from sqlalchemy import select, update

from app.core.config import Settings, get_settings
from app.db.models import AuthSession, AuthUser, Principal, SecurityAuditEvent
from app.db.session import get_sessionmaker


class SecurityFailure(RuntimeError):
    def __init__(self, code: str, message: str = "请求无法完成") -> None:
        self.code = code
        self.message = message
        super().__init__(code)


@dataclass(frozen=True)
class SecurityContext:
    session_digest: str
    user_id: str
    principal_id: str
    role: str
    store_id: str | None
    customer_ref: str | None
    account_type: str
    csrf_digest: str
    auth_version: int
    idle_expires_at: datetime
    absolute_expires_at: datetime


class AuthService:
    _hasher = PasswordHasher(
        time_cost=3,
        memory_cost=65_536,
        parallelism=2,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )
    # Verification for a missing username follows the same expensive Argon2id path.
    _dummy_hash = _hasher.hash("stage7-nonexistent-account-dummy-password")

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @staticmethod
    def normalize_username(username: str) -> str:
        return unicodedata.normalize("NFKC", username).strip().casefold()

    @classmethod
    def hash_password(cls, password: str) -> str:
        if len(password) < 12:
            raise SecurityFailure("PASSWORD_POLICY", "密码不符合安全要求")
        return cls._hasher.hash(password)

    @staticmethod
    def _digest(raw: str, secret_value: str) -> str:
        return hmac.new(secret_value.encode(), raw.encode(), hashlib.sha256).hexdigest()

    def session_digest(self, raw: str) -> str:
        return self._digest(raw, self.settings.session_secret.get_secret_value())

    def csrf_digest(self, raw: str) -> str:
        return self._digest(raw, self.settings.csrf_secret.get_secret_value())

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    async def authenticate(
        self, username: str, password: str, client_fingerprint: str
    ) -> tuple[str, str, SecurityContext]:
        now = datetime.now(UTC)
        normalized = self.normalize_username(username)
        async with get_sessionmaker()() as session:
            row = (
                await session.execute(
                    select(AuthUser, Principal)
                    .join(Principal, Principal.id == AuthUser.principal_id)
                    .where(AuthUser.username_normalized == normalized)
                    .with_for_update()
                )
            ).one_or_none()
            user = row[0] if row else None
            principal = row[1] if row else None
            password_hash = user.password_hash if user else self._dummy_hash
            verified = False
            try:
                verified = self._hasher.verify(password_hash, password)
            except (VerifyMismatchError, InvalidHashError):
                verified = False

            locked = bool(user and user.locked_until and self._utc(user.locked_until) > now)
            valid = bool(
                user and principal and user.active and principal.active and verified and not locked
            )
            if not valid:
                if user:
                    user.failed_attempts += 1
                    if user.failed_attempts >= self.settings.auth_lock_threshold:
                        user.locked_until = now + timedelta(seconds=self.settings.auth_lock_seconds)
                    user.updated_at = now
                session.add(
                    self._audit(
                        "LOGIN_FAILED",
                        "AUTH-LOGIN-001",
                        None,
                        "denied",
                        client_fingerprint,
                    )
                )
                await session.commit()
                raise SecurityFailure("INVALID_CREDENTIALS", "用户名或密码错误")

            assert user is not None and principal is not None
            user.failed_attempts = 0
            user.locked_until = None
            user.updated_at = now
            # Session fixation prevention: a successful login always creates a new token.
            raw_session = secrets.token_urlsafe(32)
            raw_csrf = secrets.token_urlsafe(32)
            idle_expiry = now + timedelta(seconds=self.settings.session_idle_seconds)
            absolute_expiry = now + timedelta(seconds=self.settings.session_absolute_seconds)
            record = AuthSession(
                token_digest=self.session_digest(raw_session),
                user_id=user.id,
                csrf_digest=self.csrf_digest(raw_csrf),
                auth_version=user.auth_version,
                created_at=now,
                last_seen_at=now,
                idle_expires_at=idle_expiry,
                absolute_expires_at=absolute_expiry,
                revoked_at=None,
                revoke_reason=None,
            )
            session.add(record)
            session.add(
                self._audit(
                    "LOGIN_SUCCEEDED",
                    "AUTH-LOGIN-002",
                    principal.id,
                    "allowed",
                    client_fingerprint,
                )
            )
            await session.commit()
            return raw_session, raw_csrf, self._context(record, user, principal)

    async def authenticate_session(
        self, raw_token: str | None, *, touch: bool = True
    ) -> SecurityContext:
        if not raw_token:
            raise SecurityFailure("AUTH_REQUIRED", "需要登录")
        now = datetime.now(UTC)
        digest = self.session_digest(raw_token)
        async with get_sessionmaker()() as session:
            row = (
                await session.execute(
                    select(AuthSession, AuthUser, Principal)
                    .join(AuthUser, AuthUser.id == AuthSession.user_id)
                    .join(Principal, Principal.id == AuthUser.principal_id)
                    .where(AuthSession.token_digest == digest)
                )
            ).one_or_none()
            if row is None:
                raise SecurityFailure("AUTH_REQUIRED", "需要登录")
            record, user, principal = row
            invalid = (
                record.revoked_at is not None
                or self._utc(record.idle_expires_at) <= now
                or self._utc(record.absolute_expires_at) <= now
                or record.auth_version != user.auth_version
                or not user.active
                or not principal.active
            )
            if invalid:
                if record.revoked_at is None:
                    record.revoked_at = now
                    record.revoke_reason = "expired_or_identity_changed"
                    await session.commit()
                raise SecurityFailure("SESSION_EXPIRED", "会话已失效")
            if touch:
                record.last_seen_at = now
                record.idle_expires_at = min(
                    now + timedelta(seconds=self.settings.session_idle_seconds),
                    self._utc(record.absolute_expires_at),
                )
                await session.commit()
            return self._context(record, user, principal)

    async def logout(self, context: SecurityContext) -> None:
        await self._revoke_digest(context.session_digest, "logout")

    async def rotate_csrf(self, context: SecurityContext) -> str:
        raw_csrf = secrets.token_urlsafe(32)
        async with get_sessionmaker()() as session:
            record = await session.get(AuthSession, context.session_digest, with_for_update=True)
            if record is None or record.revoked_at is not None:
                raise SecurityFailure("AUTH_REQUIRED", "需要登录")
            record.csrf_digest = self.csrf_digest(raw_csrf)
            await session.commit()
        return raw_csrf

    async def change_password(
        self, context: SecurityContext, current_password: str, new_password: str
    ) -> None:
        now = datetime.now(UTC)
        async with get_sessionmaker()() as session:
            user = await session.get(AuthUser, context.user_id, with_for_update=True)
            if user is None:
                raise SecurityFailure("AUTH_REQUIRED", "需要登录")
            valid: bool
            try:
                valid = self._hasher.verify(user.password_hash, current_password)
            except (VerifyMismatchError, InvalidHashError):
                valid = False
            if not valid:
                raise SecurityFailure("INVALID_CREDENTIALS", "用户名或密码错误")
            user.password_hash = self.hash_password(new_password)
            user.auth_version += 1
            user.password_changed_at = now
            user.updated_at = now
            await session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now, revoke_reason="password_changed")
            )
            session.add(
                self._audit(
                    "PASSWORD_CHANGED",
                    "AUTH-PASSWORD-001",
                    context.principal_id,
                    "allowed",
                    None,
                )
            )
            await session.commit()

    async def revoke_user_sessions(self, user_id: str, reason: str) -> None:
        now = datetime.now(UTC)
        async with get_sessionmaker()() as session:
            user = await session.get(AuthUser, user_id, with_for_update=True)
            if user is None:
                raise SecurityFailure("NOT_FOUND")
            user.auth_version += 1
            user.updated_at = now
            await session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now, revoke_reason=reason[:48])
            )
            await session.commit()

    async def update_authorization(
        self, user_id: str, role: str, store_id: str | None, actor_principal_id: str
    ) -> None:
        now = datetime.now(UTC)
        async with get_sessionmaker()() as session:
            row = (
                await session.execute(
                    select(AuthUser, Principal)
                    .join(Principal, Principal.id == AuthUser.principal_id)
                    .where(AuthUser.id == user_id)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise SecurityFailure("NOT_FOUND")
            user, principal = row
            principal.role = role
            principal.store_id = store_id
            user.auth_version += 1
            user.updated_at = now
            await session.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now, revoke_reason="authorization_changed")
            )
            session.add(
                self._audit(
                    "AUTHORIZATION_CHANGED",
                    "AUTH-RBAC-001",
                    actor_principal_id,
                    "allowed",
                    None,
                )
            )
            await session.commit()

    async def _revoke_digest(self, digest: str, reason: str) -> None:
        async with get_sessionmaker()() as session:
            await session.execute(
                update(AuthSession)
                .where(AuthSession.token_digest == digest, AuthSession.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC), revoke_reason=reason[:48])
            )
            await session.commit()

    def validate_csrf(self, context: SecurityContext, raw_csrf: str | None) -> None:
        if not raw_csrf or not hmac.compare_digest(context.csrf_digest, self.csrf_digest(raw_csrf)):
            raise SecurityFailure("CSRF_INVALID", "请求校验失败")

    @staticmethod
    def _context(record: AuthSession, user: AuthUser, principal: Principal) -> SecurityContext:
        return SecurityContext(
            session_digest=record.token_digest,
            user_id=user.id,
            principal_id=principal.id,
            role=principal.role,
            store_id=principal.store_id,
            customer_ref=principal.customer_ref,
            account_type=user.account_type,
            csrf_digest=record.csrf_digest,
            auth_version=user.auth_version,
            idle_expires_at=AuthService._utc(record.idle_expires_at),
            absolute_expires_at=AuthService._utc(record.absolute_expires_at),
        )

    @staticmethod
    def _audit(
        event_type: str,
        rule_id: str,
        actor: str | None,
        outcome: str,
        fingerprint: str | None,
    ) -> SecurityAuditEvent:
        return SecurityAuditEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            rule_id=rule_id,
            actor_principal_id=actor,
            target_ref=None,
            outcome=outcome,
            client_fingerprint=fingerprint,
            details={},
            created_at=datetime.now(UTC),
        )
