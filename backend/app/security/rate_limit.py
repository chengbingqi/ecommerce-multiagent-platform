from __future__ import annotations

import hashlib
import ipaddress
import secrets
import time
from dataclasses import dataclass
from typing import Any, cast

from fastapi import Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings, get_settings


class RateLimitFailure(RuntimeError):
    def __init__(self, code: str, retry_after: int, unavailable: bool = False) -> None:
        self.code = code
        self.retry_after = max(1, retry_after)
        self.unavailable = unavailable
        super().__init__(code)


@dataclass(frozen=True)
class RateRule:
    rule_id: str
    limit: int
    window_seconds: int
    fail_closed: bool = True


_FIXED_WINDOW_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""

_CONCURRENCY_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[3]) then return {0, count} end
redis.call('ZADD', KEYS[1], ARGV[2], ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[5])
return {1, count + 1}
"""


class RateLimiter:
    def __init__(self, settings: Settings | None = None, redis: Redis | None = None) -> None:
        self.settings = settings or get_settings()
        self.redis = redis or Redis.from_url(
            self.settings.redis_url.get_secret_value(), encoding="utf-8", decode_responses=True
        )

    async def enforce(self, rule: RateRule, subject: str) -> None:
        digest = hashlib.sha256(subject.encode()).hexdigest()[:32]
        bucket = int(time.time()) // rule.window_seconds
        key = f"{self.settings.rate_limit_prefix}:{rule.rule_id}:{digest}:{bucket}"
        try:
            current, ttl = await cast(Any, self.redis.eval)(
                _FIXED_WINDOW_LUA, 1, key, str(rule.window_seconds)
            )
        except RedisError as exc:
            if rule.fail_closed:
                raise RateLimitFailure("RATE_LIMIT_DEPENDENCY_UNAVAILABLE", 30, True) from exc
            return
        if int(current) > rule.limit:
            raise RateLimitFailure(
                "RATE_LIMITED", int(ttl) if int(ttl) > 0 else rule.window_seconds
            )

    async def close(self) -> None:
        await self.redis.aclose()

    async def acquire_concurrency(
        self, rule_id: str, subject: str, limit: int, lease_seconds: int = 120
    ) -> tuple[str, str]:
        digest = hashlib.sha256(subject.encode()).hexdigest()[:32]
        key = f"{self.settings.rate_limit_prefix}:{rule_id}:{digest}:active"
        lease = secrets.token_urlsafe(18)
        now = int(time.time())
        try:
            allowed, _ = await cast(Any, self.redis.eval)(
                _CONCURRENCY_LUA,
                1,
                key,
                str(now),
                str(now + lease_seconds),
                str(limit),
                lease,
                str(lease_seconds + 5),
            )
        except RedisError as exc:
            raise RateLimitFailure("RATE_LIMIT_DEPENDENCY_UNAVAILABLE", 30, True) from exc
        if not int(allowed):
            raise RateLimitFailure("RATE_LIMITED", lease_seconds)
        return key, lease

    async def release_concurrency(self, key: str, lease: str) -> None:
        try:
            await self.redis.zrem(key, lease)
        except RedisError:
            # Lease expiry is the safe fallback; never expose connection details.
            return


def trusted_client_ip(request: Request, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    peer = request.client.host if request.client else "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
        trusted = any(
            peer_ip in ipaddress.ip_network(cidr, strict=False)
            for cidr in settings.trusted_proxy_cidr_list
        )
    except ValueError:
        trusted = False
    if trusted:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        try:
            return str(ipaddress.ip_address(forwarded)) if forwarded else peer
        except ValueError:
            return peer
    return peer


def client_fingerprint(request: Request) -> str:
    return hashlib.sha256(trusted_client_ip(request).encode()).hexdigest()[:32]
