from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError

from app.api.v1.runs import _demo_run_limits
from app.core.config import Settings
from app.security.rate_limit import RateLimiter, RateLimitFailure, RateRule


def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="redis://localhost:6379/15",
    )


class FakeRedis:
    def __init__(self, responses: list[list[int]] | None = None, failure: bool = False) -> None:
        self.responses = responses or []
        self.failure = failure

    async def eval(self, *_args):
        if self.failure:
            raise ConnectionError("unavailable")
        return self.responses.pop(0)


class ConcurrencyRedis:
    def __init__(self) -> None:
        self.leases: dict[str, set[str]] = {}

    async def eval(self, script: str, _keys: int, key: str, *args: str) -> list[int]:
        if "ZCARD" not in script:
            return [1, 60]
        limit = int(args[2])
        lease = args[3]
        active = self.leases.setdefault(key, set())
        if len(active) >= limit:
            return [0, len(active)]
        active.add(lease)
        return [1, len(active)]

    async def zrem(self, key: str, lease: str) -> None:
        self.leases.setdefault(key, set()).discard(lease)


@pytest.mark.asyncio
async def test_atomic_limit_returns_retry_after() -> None:
    limiter = RateLimiter(settings(), FakeRedis([[2, 37]]))  # type: ignore[arg-type]
    with pytest.raises(RateLimitFailure) as captured:
        await limiter.enforce(RateRule("RL-TEST", 1, 60), "subject")
    assert captured.value.retry_after == 37
    assert not captured.value.unavailable


@pytest.mark.asyncio
async def test_high_cost_rule_fails_closed_when_redis_is_unavailable() -> None:
    limiter = RateLimiter(settings(), FakeRedis(failure=True))  # type: ignore[arg-type]
    with pytest.raises(RateLimitFailure) as captured:
        await limiter.enforce(RateRule("RL-HIGH-COST", 1, 60), "subject")
    assert captured.value.unavailable
    assert captured.value.code == "RATE_LIMIT_DEPENDENCY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_public_demo_global_concurrency_cannot_be_bypassed_by_new_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = settings().model_copy(
        update={
            "public_demo_session_concurrent_runs": 1,
            "public_demo_global_concurrent_runs": 1,
        }
    )
    redis = ConcurrencyRedis()
    limiter = RateLimiter(configured, redis)  # type: ignore[arg-type]
    monkeypatch.setattr("app.api.v1.runs.get_settings", lambda: configured)
    request = type(
        "RequestStub",
        (),
        {"client": type("Client", (), {"host": "203.0.113.10"})(), "headers": {}},
    )()

    first = await _demo_run_limits(
        limiter,
        request,  # type: ignore[arg-type]
        "public-demo-001",
        "a" * 64,
    )
    with pytest.raises(RateLimitFailure) as captured:
        await _demo_run_limits(
            limiter,
            request,  # type: ignore[arg-type]
            "public-demo-001",
            "b" * 64,
        )

    assert captured.value.code == "RATE_LIMITED"
    await limiter.release_concurrency(*first[0])
    await limiter.release_concurrency(*first[1])


@pytest.mark.asyncio
async def test_public_demo_daily_run_limit_is_principal_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = settings()
    calls: list[tuple[str, str, int]] = []

    class RecordingLimiter:
        async def enforce(self, rule: RateRule, subject: str) -> None:
            calls.append((rule.rule_id, subject, rule.limit))

        async def acquire_concurrency(
            self, rule_id: str, subject: str, _limit: int, *, lease_seconds: int
        ) -> tuple[str, str]:
            assert lease_seconds == 120
            return rule_id, subject

        async def release_concurrency(self, _key: str, _lease: str) -> None:
            return None

    monkeypatch.setattr("app.api.v1.runs.get_settings", lambda: configured)
    request = type(
        "RequestStub",
        (),
        {"client": type("Client", (), {"host": "203.0.113.10"})(), "headers": {}},
    )()
    await _demo_run_limits(
        RecordingLimiter(),  # type: ignore[arg-type]
        request,  # type: ignore[arg-type]
        "public-demo-001",
        "a" * 64,
    )

    assert (
        "RL-DEMO-ACCOUNT-DAY-001",
        "public-demo-001",
        configured.public_demo_account_runs_per_day,
    ) in calls
