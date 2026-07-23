from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.base import Base
from app.db.models import AuthSession, AuthUser, Principal, Store
from app.security.service import AuthService, SecurityFailure


@pytest_asyncio.fixture
async def auth_factory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage7-auth.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        session.add(Store(id="store-001", scope_key="store-001", name="测试店铺", created_at=now))
        session.add(
            Principal(
                id="customer-001",
                role="customer",
                store_id="store-001",
                customer_ref="customer-ref-001",
                active=True,
            )
        )
        session.add(
            AuthUser(
                id="user-001",
                principal_id="customer-001",
                username_normalized="customer",
                password_hash=AuthService.hash_password("correct horse battery staple"),
                auth_version=1,
                failed_attempts=0,
                locked_until=None,
                active=True,
                password_changed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    monkeypatch.setattr("app.security.service.get_sessionmaker", lambda: factory)
    yield factory
    await engine.dispose()


def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="redis://localhost:6379/15",
        session_secret="session-secret-at-least-thirty-two-characters",
        csrf_secret="csrf-secret-at-least-thirty-two-characters",
        auth_lock_threshold=3,
    )


@pytest.mark.asyncio
async def test_login_persists_only_digests_and_session_is_recoverable(auth_factory) -> None:
    service = AuthService(settings())
    raw_session, raw_csrf, context = await service.authenticate(
        "Customer", "correct horse battery staple", "fingerprint"
    )
    assert context.principal_id == "customer-001"
    recovered = await service.authenticate_session(raw_session)
    service.validate_csrf(recovered, raw_csrf)
    async with auth_factory() as session:
        record = await session.scalar(select(AuthSession))
        assert record is not None
        assert raw_session not in record.token_digest
        assert raw_csrf not in record.csrf_digest


@pytest.mark.asyncio
async def test_failed_login_locks_account_and_uses_generic_error(auth_factory) -> None:
    service = AuthService(settings())
    for _ in range(3):
        with pytest.raises(SecurityFailure, match="INVALID_CREDENTIALS"):
            await service.authenticate("customer", "wrong-password", "fingerprint")
    with pytest.raises(SecurityFailure, match="INVALID_CREDENTIALS"):
        await service.authenticate("customer", "correct horse battery staple", "fingerprint")


@pytest.mark.asyncio
async def test_password_change_revokes_existing_session(auth_factory) -> None:
    service = AuthService(settings())
    raw_session, _, context = await service.authenticate(
        "customer", "correct horse battery staple", "fingerprint"
    )
    await service.change_password(
        context, "correct horse battery staple", "new correct horse battery staple"
    )
    with pytest.raises(SecurityFailure, match="SESSION_EXPIRED"):
        await service.authenticate_session(raw_session)
