import asyncio
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.customer_service.repository import RepositoryFailure
from app.core.config import Settings
from app.db.models import MediaTask, Principal
from app.main import app
from app.media.asr import ASRResult
from app.media.pipeline import MediaPipeline, PipelineFailure
from app.media.probe import ProbeResult
from app.media.repository import MediaRepository
from app.media.schemas import ClipTaskCreateRequest, UploadCreateRequest
from app.media.service import MediaService
from app.media.storage import ObjectStorageError


async def create_ready_asset(factory, fixed_video: Path):
    repository = MediaRepository(factory)
    async with factory() as session:
        store_id = await session.scalar(
            select(Principal.store_id).where(Principal.id == "content-operator-001")
        )
    upload = await repository.create_upload(
        UploadCreateRequest(
            principal_id="content-operator-001",
            store_id=str(store_id),
            display_filename="resilience.mp4",
            idempotency_key="resilience-upload-001",
        ),
        3,
    )
    await repository.mark_uploaded(
        upload.upload_id,
        "content-operator-001",
        fixed_video.stat().st_size,  # noqa: ASYNC240
    )
    return await repository.finalize_asset(
        upload.upload_id,
        "content-operator-001",
        content_hash=hashlib.sha256(fixed_video.read_bytes()).hexdigest(),  # noqa: ASYNC240
        bucket="media-original",
        object_key="store/owner/resilience/original.mp4",
        mime_type="video/mp4",
        duration_ms=10_000,
        width=320,
        height=180,
        fps=Decimal("25"),
        video_codec="h264",
        audio_codec="aac",
    )


def request_for(suffix: str) -> ClipTaskCreateRequest:
    return ClipTaskCreateRequest(
        principal_id="content-operator-001",
        product_keywords=["耳机"],
        min_clip_seconds=3,
        max_clip_seconds=8,
        max_candidates=2,
        subtitle_language="zh",
        idempotency_key=f"resilience-task-{suffix}",
        run_id=f"run-resilience-{suffix}",
        thread_id=f"thread-resilience-{suffix}",
        trace_id=f"trace-resilience-{suffix}",
    )


def test_openapi_media_contract_is_complete_and_does_not_expose_storage() -> None:
    schema = app.openapi()
    required = {
        "/api/v1/media/uploads",
        "/api/v1/media/uploads/{upload_id}",
        "/api/v1/media/uploads/{upload_id}/complete",
        "/api/v1/media/{asset_id}",
        "/api/v1/media/{asset_id}/clip-tasks",
        "/api/v1/media/{asset_id}/ground-truth/context",
        "/api/v1/media/{asset_id}/ground-truth",
        "/api/v1/media/{asset_id}/ground-truth/{annotation_version}/export",
        "/api/v1/media/reviews/automated",
        "/api/v1/media/reviews/pending",
        "/api/v1/tasks/{task_id}",
        "/api/v1/tasks/{task_id}/events",
        "/api/v1/tasks/{task_id}/cancel",
        "/api/v1/media/clips/{clip_id}/download",
        "/api/v1/media/clips/{clip_id}/reviews",
    }
    assert required.issubset(schema["paths"])
    serialized = json.dumps(schema)
    assert "MINIO_SECRET_KEY" not in serialized
    assert "minio_secret_key" not in serialized
    assert "video_object_key" not in serialized


def test_upload_classification_rejects_synthetic_human_review() -> None:
    with pytest.raises(ValidationError):
        UploadCreateRequest(
            principal_id="admin-001",
            store_id="store-001",
            display_filename="synthetic.mp4",
            idempotency_key="synthetic-invalid-001",
            evaluation_scope="human_quality",
            fixture_type="synthetic_color",
            human_review_eligible=True,
        )
    fixture = UploadCreateRequest(
        principal_id="admin-001",
        store_id="store-001",
        display_filename="synthetic.mp4",
        idempotency_key="synthetic-valid-001",
        evaluation_scope="automated_pipeline",
        fixture_type="synthetic_color",
        human_review_eligible=False,
    )
    assert fixture.human_review_eligible is False


def test_production_media_gate_requires_nonplaceholder_credentials() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            database_url="postgresql+asyncpg://u:p@db/name",
            redis_url="redis://:p@redis/0",
            agent_llm_mode="deepseek",
            enable_dev_principal_input=False,
            media_enabled=True,
        )
    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://u:p@db/name",
        redis_url="redis://:p@redis/0",
        agent_llm_mode="deepseek",
        enable_dev_principal_input=False,
        media_enabled=True,
        minio_access_key="nondefault-access",
        minio_secret_key="nondefault-password",
    )
    assert "nondefault-password" not in repr(settings)


@pytest.mark.asyncio
async def test_production_media_api_rejects_development_principal_input(
    stage5_factory: async_sessionmaker[AsyncSession], fake_storage
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        database_url="postgresql+asyncpg://u:p@db/name",
        redis_url="redis://:p@redis/0",
        agent_llm_mode="deepseek",
        enable_dev_principal_input=False,
        media_enabled=True,
        minio_access_key="nondefault-access",
        minio_secret_key="nondefault-password",
    )
    service = MediaService(
        repository=MediaRepository(stage5_factory),
        storage=fake_storage,
        settings=settings,
        enqueue=lambda *_: None,
    )
    with pytest.raises(RepositoryFailure) as error:
        await service.create_upload(
            UploadCreateRequest(
                principal_id="admin-001",
                store_id="forged-store",
                display_filename="forged.mp4",
                idempotency_key="production-gate-001",
            )
        )
    assert error.value.code == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_broker_failure_is_retryable_without_duplicate_task(
    stage5_factory: async_sessionmaker[AsyncSession],
    stage5_settings,
    fixed_video: Path,
    fake_storage,
) -> None:
    asset = await create_ready_asset(stage5_factory, fixed_video)
    calls = 0

    def fail_enqueue(*_args) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("broker unavailable")

    failed_service = MediaService(
        repository=MediaRepository(stage5_factory),
        storage=fake_storage,
        settings=stage5_settings,
        enqueue=fail_enqueue,
    )
    with pytest.raises(RepositoryFailure) as exc:
        await failed_service.create_clip_task(asset.id, request_for("broker"))
    assert exc.value.code == "TRANSIENT_ERROR"
    recovered: list[str] = []
    recovered_service = MediaService(
        repository=MediaRepository(stage5_factory),
        storage=fake_storage,
        settings=stage5_settings,
        enqueue=lambda _a, task_id, _r, _v: recovered.append(task_id),
    )
    task = await recovered_service.create_clip_task(asset.id, request_for("broker"))
    async with stage5_factory() as session:
        count = await session.scalar(select(func.count(MediaTask.id)))
    assert count == 1
    assert recovered == [task.task_id]
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["timeout", "permanent"])
async def test_asr_timeout_fails_safely_without_candidates(
    failure_mode: str,
    stage5_factory: async_sessionmaker[AsyncSession],
    stage5_settings,
    fixed_video: Path,
    fake_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MediaRepository(stage5_factory)
    asset = await create_ready_asset(stage5_factory, fixed_video)
    task, _ = await repository.create_task(asset.id, request_for("asr-timeout"))
    fake_storage.objects[("media-original", asset.object_key)] = fixed_video.read_bytes()  # noqa: ASYNC240

    async def fake_probe(*_args, **_kwargs) -> ProbeResult:
        return ProbeResult(
            container="mov,mp4",
            duration_ms=10_000,
            width=320,
            height=180,
            fps=Decimal("25"),
            video_codec="h264",
            audio_codec="aac",
            video_streams=1,
            audio_streams=1,
        )

    async def fake_command(*_args, **_kwargs) -> tuple[str, str]:
        return "", ""

    class SlowASR:
        async def transcribe(self, *_args, **_kwargs) -> ASRResult:
            if failure_mode == "permanent":
                raise RuntimeError("controlled ASR failure")
            await asyncio.sleep(0.1)
            return ASRResult(language="zh", segments=[])

    monkeypatch.setattr("app.media.pipeline.probe_media_file", fake_probe)
    monkeypatch.setattr("app.media.pipeline._command", fake_command)
    fast_timeout = stage5_settings.model_copy(update={"media_subprocess_timeout_seconds": 0.01})
    pipeline = MediaPipeline(
        stage5_factory,
        fake_storage,
        fast_timeout,
        SlowASR(),  # type: ignore[arg-type]
        resume_graph=lambda _task: asyncio.sleep(0),
    )
    with pytest.raises(PipelineFailure) as exc:
        await pipeline.run(asset.id, task.id, task.run_id, task.pipeline_version)
    assert exc.value.code == ("ASR_TIMEOUT" if failure_mode == "timeout" else "ASR_FAILED")
    async with stage5_factory() as session:
        current = await session.get(MediaTask, task.id)
        candidate_count = await session.scalar(
            select(func.count()).select_from(
                __import__("app.db.models", fromlist=["ClipCandidate"]).ClipCandidate
            )
        )
    assert current and current.status == "FAILED"
    assert candidate_count == 0


@pytest.mark.asyncio
async def test_persisted_events_support_last_event_resume(
    stage5_factory: async_sessionmaker[AsyncSession], fixed_video: Path
) -> None:
    repository = MediaRepository(stage5_factory)
    asset = await create_ready_asset(stage5_factory, fixed_video)
    task, _ = await repository.create_task(asset.id, request_for("events"))
    await repository.cancel(task.id, "content-operator-001")
    all_events = await repository.list_events(task.id, "content-operator-001", 0)
    resumed = await repository.list_events(task.id, "content-operator-001", 1)
    assert len(all_events) == 2
    assert [int(row.event_id) for row in resumed] == [2]
    fresh_repository = MediaRepository(stage5_factory)
    persisted = await fresh_repository.get_task(task.id, "content-operator-001")
    assert persisted.status == "CANCELLING"


@pytest.mark.asyncio
async def test_object_storage_transient_failure_is_bounded_and_persisted_for_retry(
    stage5_factory: async_sessionmaker[AsyncSession],
    stage5_settings,
    fixed_video: Path,
    fake_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MediaRepository(stage5_factory)
    asset = await create_ready_asset(stage5_factory, fixed_video)
    task, _ = await repository.create_task(asset.id, request_for("storage-transient"))

    async def unavailable(*_args, **_kwargs) -> None:
        raise ObjectStorageError("controlled object storage outage")

    monkeypatch.setattr(fake_storage, "get_file", unavailable)
    pipeline = MediaPipeline(
        stage5_factory,
        fake_storage,
        stage5_settings,
        resume_graph=lambda _task: asyncio.sleep(0),
    )
    with pytest.raises(PipelineFailure) as error:
        await pipeline.run(asset.id, task.id, task.run_id, task.pipeline_version)
    assert error.value.code == "OBJECT_STORAGE_TRANSIENT"
    assert error.value.retryable is True
    async with stage5_factory() as session:
        current = await session.get(MediaTask, task.id)
    assert current and current.status == "FAILED"
    assert current.retry_count == 1
    assert current.error_code == "OBJECT_STORAGE_TRANSIENT"
