import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field
from sse_starlette.sse import EventSourceResponse

from app.agents.customer_service.repository import RepositoryFailure
from app.agents.live_video.service import LiveVideoAgentService
from app.core.config import get_settings
from app.media.schemas import (
    ClipTaskCreateRequest,
    ClipView,
    GroundTruthAnnotationContext,
    GroundTruthAnnotationView,
    GroundTruthCreateRequest,
    GroundTruthExportView,
    MediaAssetView,
    ReviewRequest,
    ReviewView,
    TaskEventView,
    TaskView,
    TranscriptView,
    UploadCreateRequest,
    UploadProgress,
    UploadView,
)
from app.media.service import MediaService
from app.security.demo import DEMO_MEDIA_RESTRICTED_MESSAGE, forbid_public_demo
from app.security.dependencies import (
    SecurityContextDependency,
    require_roles,
    resolve_principal_id,
)
from app.security.rate_limit import RateLimiter, RateLimitFailure, RateRule

router = APIRouter(tags=["stage5-media"])


class UploadApiCreateRequest(UploadCreateRequest):
    principal_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=64
    )


class ClipTaskApiCreateRequest(ClipTaskCreateRequest):
    principal_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=64
    )


class GroundTruthApiCreateRequest(GroundTruthCreateRequest):
    principal_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=64
    )


class ReviewApiRequest(ReviewRequest):
    principal_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, max_length=64
    )


def get_media_service() -> MediaService:
    return MediaService(trusted_identity=True)


MediaServiceDependency = Annotated[MediaService, Depends(get_media_service)]


def get_live_video_service() -> LiveVideoAgentService:
    return LiveVideoAgentService(trusted_identity=True)


LiveVideoDependency = Annotated[LiveVideoAgentService, Depends(get_live_video_service)]


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


def _failure(exc: RepositoryFailure) -> JSONResponse:
    code = {
        "UNAUTHENTICATED": status.HTTP_401_UNAUTHORIZED,
        "FORBIDDEN": status.HTTP_403_FORBIDDEN,
        "NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "IDEMPOTENCY_CONFLICT": status.HTTP_409_CONFLICT,
        "TRANSIENT_ERROR": status.HTTP_429_TOO_MANY_REQUESTS,
    }.get(exc.code, status.HTTP_400_BAD_REQUEST)
    return JSONResponse(
        status_code=code,
        content={
            "ok": False,
            "error": {
                "error_code": exc.code,
                "message": exc.message,
                "retryable": exc.code == "TRANSIENT_ERROR",
            },
        },
    )


@router.post("/media/uploads", response_model=UploadView, status_code=status.HTTP_201_CREATED)
async def create_upload(
    request: UploadApiCreateRequest,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    limiter: RateLimiterDependency,
) -> UploadView | JSONResponse:
    try:
        forbid_public_demo(context, DEMO_MEDIA_RESTRICTED_MESSAGE)
        principal_id = resolve_principal_id(request.principal_id, context)
        require_roles(context, "content_operator", "admin")
        request = request.model_copy(update={"principal_id": principal_id})
        await _enforce_rate(
            limiter,
            RateRule("RL-UPLOAD-001", get_settings().rate_upload_daily, 86_400),
            principal_id,
        )
        return await service.create_upload(request)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.put("/media/uploads/{upload_id}", response_model=UploadProgress)
async def upload_bytes(
    upload_id: str,
    request: Request,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> UploadProgress | JSONResponse:
    try:
        forbid_public_demo(context, DEMO_MEDIA_RESTRICTED_MESSAGE)
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "content_operator", "admin")
        return await service.receive_upload(upload_id, principal_id, request)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.post("/media/uploads/{upload_id}/complete", response_model=MediaAssetView)
async def complete_upload(
    upload_id: str,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> MediaAssetView | JSONResponse:
    try:
        forbid_public_demo(context, DEMO_MEDIA_RESTRICTED_MESSAGE)
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "content_operator", "admin")
        return await service.complete_upload(upload_id, principal_id)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get("/media/{asset_id}", response_model=MediaAssetView)
async def get_asset(
    asset_id: str,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> MediaAssetView | JSONResponse:
    try:
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "content_operator", "admin")
        return await service.get_asset(asset_id, principal_id)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get("/media/{asset_id}/content", response_model=None)
async def get_asset_content(
    asset_id: str,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> StreamingResponse | JSONResponse:
    try:
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "content_operator", "admin")
        stream, content_type, filename = await service.asset_content(asset_id, principal_id)
        return StreamingResponse(
            stream,
            media_type=content_type,
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get(
    "/media/{asset_id}/ground-truth/context",
    response_model=GroundTruthAnnotationContext,
)
async def get_ground_truth_context(
    asset_id: str,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> GroundTruthAnnotationContext | JSONResponse:
    try:
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "admin")
        return await service.ground_truth_context(asset_id, principal_id)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.post(
    "/media/{asset_id}/ground-truth",
    response_model=GroundTruthAnnotationView,
    status_code=status.HTTP_201_CREATED,
)
async def create_ground_truth(
    asset_id: str,
    request: GroundTruthApiCreateRequest,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
) -> GroundTruthAnnotationView | JSONResponse:
    try:
        forbid_public_demo(context, DEMO_MEDIA_RESTRICTED_MESSAGE)
        principal_id = resolve_principal_id(request.principal_id, context)
        require_roles(context, "admin")
        request = request.model_copy(update={"principal_id": principal_id})
        return await service.create_ground_truth(asset_id, request)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get(
    "/media/{asset_id}/ground-truth/{annotation_version}/export",
    response_model=GroundTruthExportView,
)
async def export_ground_truth(
    asset_id: str,
    annotation_version: str,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> GroundTruthExportView | JSONResponse:
    try:
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "admin")
        return await service.ground_truth_export(asset_id, annotation_version, principal_id)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.post(
    "/media/{asset_id}/clip-tasks",
    response_model=TaskView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_clip_task(
    asset_id: str,
    request: ClipTaskApiCreateRequest,
    service: LiveVideoDependency,
    context: SecurityContextDependency,
    limiter: RateLimiterDependency,
) -> TaskView | JSONResponse:
    try:
        forbid_public_demo(context, DEMO_MEDIA_RESTRICTED_MESSAGE)
        principal_id = resolve_principal_id(request.principal_id, context)
        require_roles(context, "content_operator", "admin")
        request = request.model_copy(update={"principal_id": principal_id})
        await _enforce_rate(
            limiter,
            RateRule("RL-MEDIA-001", get_settings().rate_media_daily, 86_400),
            principal_id,
        )
        return await service.create_run(asset_id, request)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get("/tasks/{task_id}", response_model=TaskView)
async def get_task(
    task_id: str,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> TaskView | JSONResponse:
    try:
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "content_operator", "admin")
        return await service.get_task(task_id, principal_id)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get("/tasks/{task_id}/events", response_model=list[TaskEventView])
async def get_events(
    task_id: str,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
    after: int = Query(default=0, ge=0),
) -> list[TaskEventView] | JSONResponse:
    try:
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "content_operator", "admin")
        return await service.events(task_id, principal_id, after)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get("/tasks/{task_id}/events/stream", response_model=None)
async def stream_events(
    task_id: str,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    limiter: RateLimiterDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse | JSONResponse:
    principal_id = resolve_principal_id(principal_id, context)
    require_roles(context, "content_operator", "admin")
    try:
        lease_key, lease = await limiter.acquire_concurrency(
            "RL-SSE-001", principal_id, get_settings().rate_sse_concurrent
        )
    except RateLimitFailure as exc:
        raise HTTPException(
            status_code=503 if exc.unavailable else 429,
            headers={"Retry-After": str(exc.retry_after)},
            detail={"error_code": exc.code, "rule_id": "RL-SSE-001"},
        ) from exc
    try:
        await service.get_task(task_id, principal_id)
    except RepositoryFailure as exc:
        await limiter.release_concurrency(lease_key, lease)
        return _failure(exc)
    try:
        cursor = max(int(last_event_id or "0"), 0)
    except ValueError:
        cursor = 0

    async def generate() -> AsyncIterator[dict[str, str]]:
        nonlocal cursor
        try:
            while True:
                events = await service.events(task_id, principal_id, cursor)
                for event in events:
                    cursor = int(event.event_id)
                    yield {
                        "id": event.event_id,
                        "event": event.event_type,
                        "data": json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
                    }
                snapshot = await service.get_task(task_id, principal_id)
                if snapshot.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    return
                await asyncio.sleep(1)
        finally:
            await limiter.release_concurrency(lease_key, lease)

    return EventSourceResponse(generate(), ping=15)


@router.post("/tasks/{task_id}/cancel", response_model=TaskView)
async def cancel_task(
    task_id: str,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> TaskView | JSONResponse:
    try:
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "content_operator", "admin")
        return await service.cancel(task_id, principal_id)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get("/tasks/{task_id}/clips", response_model=list[ClipView])
async def list_clips(
    task_id: str,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> list[ClipView] | JSONResponse:
    try:
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "content_operator", "admin")
        return await service.clips(task_id, principal_id)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get("/media/reviews/pending", response_model=list[ClipView])
async def pending_review_clips(
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> list[ClipView] | JSONResponse:
    try:
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "content_operator", "admin")
        return await service.pending_review_clips(principal_id)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get("/media/reviews/automated", response_model=list[ClipView])
async def automated_review_clips(
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> list[ClipView] | JSONResponse:
    try:
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "content_operator", "admin")
        return await service.automated_review_clips(principal_id)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get("/tasks/{task_id}/transcript", response_model=TranscriptView)
async def get_transcript(
    task_id: str,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> TranscriptView | JSONResponse:
    try:
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "content_operator", "admin")
        return await service.transcript(task_id, principal_id)
    except RepositoryFailure as exc:
        return _failure(exc)


@router.get("/media/clips/{clip_id}/download", response_model=None)
async def download_clip(
    clip_id: str,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
    limiter: RateLimiterDependency,
    principal_id: str | None = Query(default=None, min_length=1, max_length=64),
    kind: Literal["video", "subtitle", "thumbnail"] = "video",
) -> StreamingResponse | JSONResponse:
    try:
        principal_id = resolve_principal_id(principal_id, context)
        require_roles(context, "content_operator", "admin")
        await _enforce_rate(
            limiter,
            RateRule("RL-DOWNLOAD-001", get_settings().rate_download_per_minute, 60),
            principal_id,
        )
        stream, content_type, filename = await service.download(clip_id, principal_id, kind)
        return StreamingResponse(
            stream,
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )
    except RepositoryFailure as exc:
        return _failure(exc)


@router.post("/media/clips/{clip_id}/reviews", response_model=ReviewView)
async def review_clip(
    clip_id: str,
    request: ReviewApiRequest,
    service: MediaServiceDependency,
    context: SecurityContextDependency,
) -> ReviewView | JSONResponse:
    try:
        forbid_public_demo(context, DEMO_MEDIA_RESTRICTED_MESSAGE)
        principal_id = resolve_principal_id(request.principal_id, context)
        require_roles(context, "content_operator", "admin")
        request = request.model_copy(update={"principal_id": principal_id})
        return await service.review(clip_id, request)
    except RepositoryFailure as exc:
        return _failure(exc)
