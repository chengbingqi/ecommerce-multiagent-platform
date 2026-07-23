from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings, get_settings
from app.security.service import AuthService, SecurityContext, SecurityFailure


def get_auth_service() -> AuthService:
    return AuthService()


AuthDependency = Annotated[AuthService, Depends(get_auth_service)]


async def security_context(request: Request, auth: AuthDependency) -> SecurityContext | None:
    cached = getattr(request.state, "security_context", None)
    if cached is not None:
        return cast(SecurityContext, cached)
    settings = get_settings()
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token and settings.app_env != "production":
        return None
    try:
        context = await auth.authenticate_session(raw_token)
    except SecurityFailure as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": exc.code, "message": exc.message},
        ) from exc
    request.state.security_context = context
    return context


SecurityContextDependency = Annotated[SecurityContext | None, Depends(security_context)]


def resolve_principal_id(
    supplied_principal_id: str | None,
    context: SecurityContext | None,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    if settings.app_env == "production":
        if supplied_principal_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error_code": "CLIENT_PRINCIPAL_FORBIDDEN",
                    "message": "身份字段不允许由客户端提供",
                },
            )
        if context is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail={"error_code": "AUTH_REQUIRED"}
            )
        return context.principal_id
    if context is not None:
        if supplied_principal_id and supplied_principal_id != context.principal_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail={"error_code": "IDENTITY_MISMATCH"}
            )
        return context.principal_id
    if settings.enable_dev_principal_input and supplied_principal_id:
        return supplied_principal_id
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail={"error_code": "AUTH_REQUIRED"}
    )


def require_roles(context: SecurityContext | None, *allowed: str) -> None:
    settings = get_settings()
    if context is None and settings.app_env != "production" and settings.enable_dev_principal_input:
        return
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail={"error_code": "AUTH_REQUIRED"}
        )
    if context.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail={"error_code": "FORBIDDEN"}
        )
