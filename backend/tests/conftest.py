from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import pytest_asyncio

from app.api.v1.health import get_redis_client
from app.core.config import get_settings
from app.db.session import get_engine
from app.main import app


@pytest.fixture(autouse=True)
def test_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://test_user:test_password@postgres/test_db"
    )
    monkeypatch.setenv("REDIS_URL", "redis://:test_password@redis:6379/0")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_redis_client.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_redis_client.cache_clear()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
