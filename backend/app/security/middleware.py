from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from app.core.config import get_settings
from app.security.rate_limit import RateLimiter, RateLimitFailure, RateRule, trusted_client_ip
from app.security.service import AuthService, SecurityFailure

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_EXEMPT_PATHS = {"/api/v1/auth/login"}


def _origin_allowed(request: Request) -> bool:
    settings = get_settings()
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    candidate = origin
    if not candidate and referer:
        parsed = urlsplit(referer)
        candidate = f"{parsed.scheme}://{parsed.netloc}"
    return bool(candidate and candidate.rstrip("/") in settings.allowed_origin_list)


async def csrf_and_origin_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    settings = get_settings()
    if settings.app_env == "production" and request.url.path != "/api/v1/health/live":
        limiter = RateLimiter(settings)
        try:
            await limiter.enforce(
                RateRule("RL-API-001", settings.rate_general_per_minute, 60),
                trusted_client_ip(request, settings),
            )
        except RateLimitFailure as exc:
            return JSONResponse(
                status_code=503 if exc.unavailable else 429,
                headers={"Retry-After": str(exc.retry_after)},
                content={
                    "ok": False,
                    "error": {"error_code": exc.code, "rule_id": "RL-API-001"},
                },
            )
    if settings.app_env != "production" or request.method not in UNSAFE_METHODS:
        return await call_next(request)
    if not _origin_allowed(request):
        return JSONResponse(
            status_code=403,
            content={
                "ok": False,
                "error": {"error_code": "ORIGIN_FORBIDDEN", "message": "请求来源不受信任"},
            },
        )
    if request.url.path in CSRF_EXEMPT_PATHS:
        return await call_next(request)
    auth = AuthService(settings)
    try:
        context = await auth.authenticate_session(
            request.cookies.get(settings.session_cookie_name), touch=False
        )
        auth.validate_csrf(context, request.headers.get(settings.csrf_header_name))
    except SecurityFailure as exc:
        return JSONResponse(
            status_code=403 if exc.code == "CSRF_INVALID" else 401,
            content={"ok": False, "error": {"error_code": exc.code, "message": exc.message}},
        )
    request.state.security_context = context
    return await call_next(request)
