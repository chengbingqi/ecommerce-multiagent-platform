from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.config import get_settings
from app.security.dependencies import (
    AuthDependency,
    SecurityContextDependency,
    get_auth_service,
    require_roles,
)
from app.security.rate_limit import (
    RateLimiter,
    RateLimitFailure,
    RateRule,
    client_fingerprint,
    trusted_client_ip,
)
from app.security.schemas import (
    AuthorizationUpdateRequest,
    CsrfResponse,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    PasswordChangeRequest,
    PrincipalView,
    SessionView,
)
from app.security.service import AuthService, SecurityContext, SecurityFailure

router = APIRouter(prefix="/auth", tags=["authentication"])


def get_rate_limiter() -> RateLimiter:
    return RateLimiter()


RateLimiterDependency = Annotated[RateLimiter, Depends(get_rate_limiter)]


def _session_view(context: SecurityContext) -> SessionView:
    settings = get_settings()
    return SessionView(
        principal=PrincipalView(
            principal_id=context.principal_id,
            role=context.role,
            store_id=context.store_id,
            customer_ref=context.customer_ref,
            account_type=context.account_type,
        ),
        idle_expires_at=context.idle_expires_at,
        absolute_expires_at=context.absolute_expires_at,
        security_policy_version=settings.security_policy_version,
    )


def _raise_security(exc: SecurityFailure) -> NoReturn:
    code = status.HTTP_401_UNAUTHORIZED
    if exc.code == "PASSWORD_POLICY":
        code = status.HTTP_400_BAD_REQUEST
    raise HTTPException(
        status_code=code, detail={"error_code": exc.code, "message": exc.message}
    ) from exc


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth: AuthDependency,
    limiter: RateLimiterDependency,
) -> LoginResponse:
    settings = get_settings()
    subject = f"{auth.normalize_username(payload.username)}:{trusted_client_ip(request)}"
    try:
        await limiter.enforce(
            RateRule("RL-LOGIN-001", settings.rate_login_per_15_minutes, 900), subject
        )
        raw_session, raw_csrf, context = await auth.authenticate(
            payload.username, payload.password, client_fingerprint(request)
        )
    except RateLimitFailure as exc:
        raise HTTPException(
            status_code=503 if exc.unavailable else 429,
            headers={"Retry-After": str(exc.retry_after)},
            detail={"error_code": exc.code, "message": "登录暂时不可用"},
        ) from exc
    except SecurityFailure as exc:
        _raise_security(exc)
    response.set_cookie(
        settings.session_cookie_name,
        raw_session,
        max_age=settings.session_absolute_seconds,
        secure=settings.secure_session_cookie,
        httponly=True,
        samesite="lax",
        path="/",
    )
    view = _session_view(context)
    return LoginResponse(**view.model_dump(), csrf_token=raw_csrf)


@router.get("/me", response_model=SessionView)
async def me(context: SecurityContextDependency) -> SessionView:
    if context is None:
        raise HTTPException(status_code=401, detail={"error_code": "AUTH_REQUIRED"})
    return _session_view(context)


@router.get("/csrf", response_model=CsrfResponse)
async def csrf(context: SecurityContextDependency, auth: AuthDependency) -> CsrfResponse:
    if context is None:
        raise HTTPException(status_code=401, detail={"error_code": "AUTH_REQUIRED"})
    try:
        return CsrfResponse(csrf_token=await auth.rotate_csrf(context))
    except SecurityFailure as exc:
        _raise_security(exc)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    response: Response, context: SecurityContextDependency, auth: AuthDependency
) -> MessageResponse:
    if context is None:
        raise HTTPException(status_code=401, detail={"error_code": "AUTH_REQUIRED"})
    await auth.logout(context)
    settings = get_settings()
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.secure_session_cookie,
        httponly=True,
        samesite="lax",
    )
    return MessageResponse()


@router.post("/password", response_model=MessageResponse)
async def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    context: SecurityContextDependency,
    auth: Annotated[AuthService, Depends(get_auth_service)],
) -> MessageResponse:
    if context is None:
        raise HTTPException(status_code=401, detail={"error_code": "AUTH_REQUIRED"})
    try:
        await auth.change_password(context, payload.current_password, payload.new_password)
    except SecurityFailure as exc:
        _raise_security(exc)
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/")
    return MessageResponse()


@router.patch("/admin/users/{user_id}/authorization", response_model=MessageResponse)
async def update_user_authorization(
    user_id: str,
    payload: AuthorizationUpdateRequest,
    context: SecurityContextDependency,
    auth: AuthDependency,
) -> MessageResponse:
    require_roles(context, "admin")
    assert context is not None
    try:
        await auth.update_authorization(
            user_id, payload.role, payload.store_id, context.principal_id
        )
    except SecurityFailure as exc:
        if exc.code == "NOT_FOUND":
            raise HTTPException(status_code=404, detail={"error_code": "NOT_FOUND"}) from exc
        _raise_security(exc)
    return MessageResponse()
