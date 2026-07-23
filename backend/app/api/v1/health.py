import asyncio
from functools import lru_cache
from typing import Literal, cast

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine
from app.security.dependencies import SecurityContextDependency

router = APIRouter(prefix="/health", tags=["health"])


class ComponentStatus(BaseModel):
    status: Literal["ok", "unavailable"]


class ReadyResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    service: Literal["api"] = "api"
    environment: str
    components: dict[str, ComponentStatus]


async def check_postgres() -> bool:
    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@lru_cache
def get_redis_client() -> Redis:
    redis_url = get_settings().redis_url.get_secret_value()
    return cast(Redis, Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2))


async def check_redis() -> bool:
    try:
        return bool(await get_redis_client().ping())
    except Exception:
        return False


async def close_redis() -> None:
    if get_redis_client.cache_info().currsize:
        await get_redis_client().aclose()
        get_redis_client.cache_clear()


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok", "service": "api"}


@router.get("/ready", response_model=ReadyResponse)
async def ready(context: SecurityContextDependency) -> ReadyResponse | JSONResponse:
    if get_settings().app_env == "production" and context is None:
        return JSONResponse(
            status_code=401,
            content={"error": {"error_code": "AUTH_REQUIRED", "message": "需要登录"}},
        )
    postgres_ok, redis_ok = await asyncio.gather(check_postgres(), check_redis())
    components = {
        "postgres": ComponentStatus(status="ok" if postgres_ok else "unavailable"),
        "redis": ComponentStatus(status="ok" if redis_ok else "unavailable"),
    }
    status = "ok" if postgres_ok and redis_ok else "unavailable"
    payload = ReadyResponse(
        status=status,
        environment=get_settings().app_env,
        components=components,
    )
    if status == "unavailable":
        return JSONResponse(status_code=503, content=payload.model_dump())
    return payload
