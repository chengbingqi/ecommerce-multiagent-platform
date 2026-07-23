from collections.abc import Awaitable, Callable

import httpx
import pytest

from app.api.v1 import health


async def test_live_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "api"}
    assert response.headers["x-request-id"]


@pytest.mark.parametrize(
    ("postgres_ok", "redis_ok", "expected_status"),
    [(True, True, 200), (False, True, 503), (True, False, 503)],
)
async def test_ready_dependency_states(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    postgres_ok: bool,
    redis_ok: bool,
    expected_status: int,
) -> None:
    def checker(result: bool) -> Callable[[], Awaitable[bool]]:
        async def check() -> bool:
            return result

        return check

    monkeypatch.setattr(health, "check_postgres", checker(postgres_ok))
    monkeypatch.setattr(health, "check_redis", checker(redis_ok))

    response = await client.get("/api/v1/health/ready")
    body = response.json()

    assert response.status_code == expected_status
    assert body["status"] == ("ok" if expected_status == 200 else "unavailable")
    assert body["environment"] == "test"
    assert body["components"]["postgres"]["status"] == ("ok" if postgres_ok else "unavailable")
    assert body["components"]["redis"]["status"] == ("ok" if redis_ok else "unavailable")
    serialized = response.text
    assert "test_password" not in serialized
    assert "DATABASE_URL" not in serialized
