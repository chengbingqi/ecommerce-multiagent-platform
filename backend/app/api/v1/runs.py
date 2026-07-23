import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import Field
from sse_starlette.sse import EventSourceResponse

from app.agents.customer_service.repository import RepositoryFailure
from app.agents.customer_service.schemas import (
    ApprovalDecisionRequest,
    CustomerServiceOutput,
    CustomerServiceRunRequest,
)
from app.agents.customer_service.service import CustomerServiceAgentService
from app.agents.live_video.schemas import LiveVideoOutput
from app.agents.live_video.service import LiveVideoAgentService
from app.agents.operations.schemas import OperationsOutput, OperationsRunRequest
from app.agents.operations.service import OperationsAgentService
from app.agents.orchestrator.schemas import (
    AgentEventView,
    OrchestratorOutput,
)
from app.agents.orchestrator.schemas import (
    OrchestratorRunRequest as CoreOrchestratorRunRequest,
)
from app.agents.orchestrator.service import OrchestratorService
from app.core.config import get_settings
from app.security.demo import (
    DEMO_ADMIN_RESTRICTED_MESSAGE,
    DEMO_MEDIA_RESTRICTED_MESSAGE,
    forbid_public_demo,
    is_public_demo,
    reject_demo_write_text,
    require_demo_order,
    require_demo_store,
)
from app.security.dependencies import (
    SecurityContextDependency,
    require_roles,
    resolve_principal_id,
)
from app.security.rate_limit import RateLimiter, RateLimitFailure, RateRule, trusted_client_ip

router = APIRouter(tags=["customer-service-runs"])


def get_customer_service() -> CustomerServiceAgentService:
    return CustomerServiceAgentService(trusted_identity=True)


ServiceDependency = Annotated[CustomerServiceAgentService, Depends(get_customer_service)]


def get_operations_service() -> OperationsAgentService:
    return OperationsAgentService(trusted_identity=True)


OperationsServiceDependency = Annotated[OperationsAgentService, Depends(get_operations_service)]


def get_orchestrator_service() -> OrchestratorService:
    return OrchestratorService(trusted_identity=True)


OrchestratorServiceDependency = Annotated[OrchestratorService, Depends(get_orchestrator_service)]


class CustomerServiceApiRunRequest(CustomerServiceRunRequest):
    principal_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=64
    )


class OperationsApiRunRequest(OperationsRunRequest):
    principal_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=64
    )


class OrchestratorRunRequest(CoreOrchestratorRunRequest):
    principal_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=64
    )


class ApprovalApiDecisionRequest(ApprovalDecisionRequest):
    principal_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=64
    )


RunRequest = CustomerServiceApiRunRequest | OperationsApiRunRequest | OrchestratorRunRequest
RunOutput = CustomerServiceOutput | OperationsOutput | LiveVideoOutput | OrchestratorOutput


def get_live_video_service() -> LiveVideoAgentService:
    return LiveVideoAgentService(trusted_identity=True)


LiveVideoServiceDependency = Annotated[LiveVideoAgentService, Depends(get_live_video_service)]


def get_rate_limiter() -> RateLimiter:
    return RateLimiter()


RateLimiterDependency = Annotated[RateLimiter, Depends(get_rate_limiter)]


async def _enforce_rate(limiter: RateLimiter, rule: RateRule, subject: str) -> None:
    try:
        await limiter.enforce(rule, subject)
    except RateLimitFailure as exc:
        raise HTTPException(
            status_code=503 if exc.unavailable else 429,
            headers={"Retry-After": str(exc.retry_after)},
            detail={"error_code": exc.code, "rule_id": rule.rule_id},
        ) from exc


async def _demo_run_limits(
    limiter: RateLimiter, request: Request, principal_id: str, session_digest: str
) -> tuple[tuple[str, str], tuple[str, str]]:
    settings = get_settings()
    await _enforce_rate(
        limiter,
        RateRule("RL-DEMO-IP-DAY-001", settings.public_demo_ip_requests_per_day, 86_400),
        trusted_client_ip(request),
    )
    await _enforce_rate(
        limiter,
        RateRule(
            "RL-DEMO-ACCOUNT-DAY-001",
            settings.public_demo_account_runs_per_day,
            86_400,
        ),
        principal_id,
    )
    await _enforce_rate(
        limiter,
        RateRule(
            "RL-DEMO-SESSION-HOUR-001",
            settings.public_demo_session_requests_per_hour,
            3_600,
        ),
        session_digest,
    )
    session_lease = await limiter.acquire_concurrency(
        "RL-DEMO-SESSION-RUN-001",
        session_digest,
        settings.public_demo_session_concurrent_runs,
        lease_seconds=120,
    )
    try:
        global_lease = await limiter.acquire_concurrency(
            "RL-DEMO-GLOBAL-RUN-001",
            principal_id,
            settings.public_demo_global_concurrent_runs,
            lease_seconds=120,
        )
    except RateLimitFailure:
        await limiter.release_concurrency(*session_lease)
        raise
    return session_lease, global_lease


def _failure(exc: RepositoryFailure) -> JSONResponse:
    status_code = {
        "UNAUTHENTICATED": status.HTTP_401_UNAUTHORIZED,
        "FORBIDDEN": status.HTTP_403_FORBIDDEN,
        "NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "IDEMPOTENCY_CONFLICT": status.HTTP_409_CONFLICT,
        "APPROVAL_REQUIRED": status.HTTP_409_CONFLICT,
    }.get(exc.code, status.HTTP_400_BAD_REQUEST)
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": {"error_code": exc.code, "message": exc.message}},
    )


@router.post(
    "/runs",
    response_model=RunOutput,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(
    request: RunRequest,
    http_request: Request,
    service: ServiceDependency,
    operations: OperationsServiceDependency,
    orchestrator: OrchestratorServiceDependency,
    context: SecurityContextDependency,
    limiter: RateLimiterDependency,
) -> RunOutput | JSONResponse:
    try:
        principal_id = resolve_principal_id(request.principal_id, context)
        auth_session_digest = context.session_digest if context else None
        if is_public_demo(context):
            assert context is not None
            reject_demo_write_text(
                context,
                str(getattr(request, "request_text", getattr(request, "message", ""))),
            )
            if isinstance(request, OrchestratorRunRequest):
                require_demo_order(context, request.order_id)
                require_demo_store(context, request.store_id)
                if request.asset_id:
                    forbid_public_demo(context, DEMO_MEDIA_RESTRICTED_MESSAGE)
            elif isinstance(request, OperationsApiRunRequest):
                require_demo_store(context, request.store_id)
            elif isinstance(request, CustomerServiceApiRunRequest):
                require_demo_order(context, request.order_id)
            else:
                forbid_public_demo(context, DEMO_MEDIA_RESTRICTED_MESSAGE)
        request = request.model_copy(
            update={"principal_id": principal_id, "auth_session_digest": auth_session_digest}
        )
        await _enforce_rate(
            limiter,
            RateRule("RL-RUN-001", get_settings().rate_run_per_minute, 60),
            f"{principal_id}:{trusted_client_ip(http_request)}",
        )
        demo_leases: tuple[tuple[str, str], tuple[str, str]] | None = None
        if is_public_demo(context):
            assert context is not None
            try:
                demo_leases = await _demo_run_limits(
                    limiter, http_request, principal_id, context.session_digest
                )
            except RateLimitFailure as exc:
                raise HTTPException(
                    status_code=503 if exc.unavailable else 429,
                    headers={"Retry-After": str(exc.retry_after)},
                    detail={"error_code": exc.code, "rule_id": "RL-DEMO-RUN"},
                ) from exc
        if get_settings().agent_llm_mode == "deepseek":
            await _enforce_rate(
                limiter,
                RateRule(
                    "RL-MODEL-001",
                    get_settings().public_demo_model_daily
                    if is_public_demo(context)
                    else get_settings().rate_model_daily,
                    86_400,
                ),
                principal_id,
            )
        try:
            if isinstance(request, OrchestratorRunRequest):
                require_roles(
                    context,
                    "customer",
                    "customer:self",
                    "store_operator",
                    "content_operator",
                    "admin",
                )
                return await orchestrator.create_run(request)
            if isinstance(request, OperationsApiRunRequest):
                require_roles(context, "store_operator", "admin")
                return await operations.create_run(request)
            require_roles(context, "customer", "customer:self", "store_operator", "admin")
            return await service.create_run(request)
        finally:
            if demo_leases:
                await limiter.release_concurrency(*demo_leases[0])
                await limiter.release_concurrency(*demo_leases[1])
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get("/runs/{run_id}", response_model=RunOutput)
async def get_run(
    run_id: str,
    service: ServiceDependency,
    operations: OperationsServiceDependency,
    live_video: LiveVideoServiceDependency,
    orchestrator: OrchestratorServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> RunOutput | JSONResponse:
    principal_id = resolve_principal_id(principal_id, context)
    auth_session_digest = context.session_digest if context else None
    try:
        return await orchestrator.get_run(run_id, principal_id, auth_session_digest)
    except RepositoryFailure as exc:
        if exc.code == "NOT_FOUND":
            try:
                return await service.get_run(run_id, principal_id, auth_session_digest)
            except RepositoryFailure as customer_exc:
                if customer_exc.code != "NOT_FOUND":
                    return _failure(customer_exc)
                try:
                    return await operations.get_run(run_id, principal_id, auth_session_digest)
                except RepositoryFailure as operations_exc:
                    if operations_exc.code != "NOT_FOUND":
                        return _failure(operations_exc)
                    if is_public_demo(context):
                        return _failure(
                            RepositoryFailure("FORBIDDEN", DEMO_MEDIA_RESTRICTED_MESSAGE)
                        )
                    try:
                        return await live_video.get_run(run_id, principal_id)
                    except RepositoryFailure as media_exc:
                        return _failure(media_exc)
        return _failure(exc)


@router.get("/runs/{run_id}/events", response_model=list[AgentEventView])
async def get_run_events(
    run_id: str,
    orchestrator: OrchestratorServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
    after_event_id: str | None = Query(default=None, max_length=64),
) -> list[AgentEventView] | JSONResponse:
    principal_id = resolve_principal_id(principal_id, context)
    auth_session_digest = context.session_digest if context else None
    try:
        return await orchestrator.list_events(
            run_id, principal_id, after_event_id, auth_session_digest
        )
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get("/runs/{run_id}/events/stream", response_model=None)
async def stream_run_events(
    run_id: str,
    orchestrator: OrchestratorServiceDependency,
    context: SecurityContextDependency,
    limiter: RateLimiterDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse | JSONResponse:
    principal_id = resolve_principal_id(principal_id, context)
    auth_session_digest = context.session_digest if context else None
    try:
        lease_key, lease = await limiter.acquire_concurrency(
            "RL-SSE-001",
            auth_session_digest
            if is_public_demo(context) and auth_session_digest
            else principal_id,
            get_settings().public_demo_sse_concurrent_per_session
            if is_public_demo(context)
            else get_settings().rate_sse_concurrent,
        )
    except RateLimitFailure as exc:
        raise HTTPException(
            status_code=503 if exc.unavailable else 429,
            headers={"Retry-After": str(exc.retry_after)},
            detail={"error_code": exc.code, "rule_id": "RL-SSE-001"},
        ) from exc
    try:
        await orchestrator.get_run(run_id, principal_id, auth_session_digest)
    except RepositoryFailure as exc:
        await limiter.release_concurrency(lease_key, lease)
        return _failure(exc)

    async def generate() -> AsyncIterator[dict[str, str]]:
        cursor = last_event_id
        try:
            while True:
                events = await orchestrator.list_events(
                    run_id, principal_id, cursor, auth_session_digest
                )
                for event in events:
                    cursor = event.event_id
                    yield {
                        "id": event.event_id,
                        "event": event.event_type,
                        "data": json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
                    }
                snapshot = await orchestrator.get_run(run_id, principal_id, auth_session_digest)
                if snapshot.overall_status in {
                    "SUCCEEDED",
                    "PARTIAL_SUCCESS",
                    "FAILED",
                    "CANCELLED",
                }:
                    return
                await asyncio.sleep(1)
        finally:
            await limiter.release_concurrency(lease_key, lease)

    return EventSourceResponse(generate(), ping=15)


@router.post("/runs/{run_id}/cancel", response_model=OrchestratorOutput)
async def cancel_run(
    run_id: str,
    orchestrator: OrchestratorServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> OrchestratorOutput | JSONResponse:
    principal_id = resolve_principal_id(principal_id, context)
    forbid_public_demo(context, "公共演示账号不允许取消或修改运行状态。")
    try:
        return await orchestrator.cancel(
            run_id, principal_id, context.session_digest if context else None
        )
    except RepositoryFailure as exc:
        return _failure(exc)


@router.post("/approvals/{approval_id}/decision", response_model=CustomerServiceOutput)
async def decide_approval(
    approval_id: str,
    request: ApprovalApiDecisionRequest,
    service: ServiceDependency,
    orchestrator: OrchestratorServiceDependency,
    context: SecurityContextDependency,
    limiter: RateLimiterDependency,
) -> CustomerServiceOutput | JSONResponse:
    try:
        principal_id = resolve_principal_id(request.principal_id, context)
        forbid_public_demo(context, DEMO_ADMIN_RESTRICTED_MESSAGE)
        require_roles(context, "approver", "admin")
        request = request.model_copy(update={"principal_id": principal_id})
        await _enforce_rate(
            limiter,
            RateRule("RL-APPROVAL-001", get_settings().rate_approval_per_minute, 60),
            principal_id,
        )
        output = await service.decide(approval_id, request)
        run_owner = await service.principal_for_run(output.run_id)
        await orchestrator.resume_parent_for_child(output.run_id, run_owner)
        return output
    except RepositoryFailure as exc:
        return _failure(exc)
