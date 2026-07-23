import asyncio
from typing import Any

from app.db.session import close_engine
from app.media.pipeline import MediaPipeline, PipelineFailure
from app.worker.celery_app import celery_app


async def _run_pipeline(
    asset_id: str, media_task_id: str, run_id: str, pipeline_version: str
) -> None:
    """Run one delivery with resources scoped to this asyncio event loop.

    Celery's prefork child invokes this synchronous task repeatedly.  Disposing the
    cached async engine before ``asyncio.run`` closes its loop prevents asyncpg
    connections from leaking into the next delivery's event loop.
    """
    try:
        await MediaPipeline().run(asset_id, media_task_id, run_id, pipeline_version)
    finally:
        await close_engine()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="media.process",
    autoretry_for=(),
    max_retries=2,
)
def process_media(
    self: Any, asset_id: str, media_task_id: str, run_id: str, pipeline_version: str
) -> dict[str, str]:
    try:
        asyncio.run(_run_pipeline(asset_id, media_task_id, run_id, pipeline_version))
    except PipelineFailure as exc:
        if exc.retryable and self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1)) from exc
        raise
    return {"task_id": media_task_id, "status": "SUCCEEDED"}
