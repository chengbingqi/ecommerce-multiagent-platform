import asyncio
import hashlib
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.live_video.graph import build_live_video_graph
from app.core.config import Settings
from app.db.models import ClipCandidate, MediaTask, MediaTaskEvent, Principal, Transcript
from app.media.asr import (
    ASRInitializationError,
    ASRResult,
    ASRSegment,
    FakeASR,
    FasterWhisperASR,
)
from app.media.pipeline import MediaPipeline, PipelineFailure, _command
from app.media.repository import MediaRepository
from app.media.schemas import ClipTaskCreateRequest, UploadCreateRequest
from app.worker import tasks as worker_tasks
from app.worker.celery_app import celery_app


async def prepared_task(factory, fixed_video: Path):
    repository = MediaRepository(factory)
    async with factory() as session:
        store_id = await session.scalar(
            select(Principal.store_id).where(Principal.id == "content-operator-001")
        )
    upload = await repository.create_upload(
        UploadCreateRequest(
            principal_id="content-operator-001",
            store_id=str(store_id),
            display_filename="fixed.mp4",
            idempotency_key="pipeline-upload-001",
        ),
        3,
    )
    await repository.mark_uploaded(
        upload.upload_id,
        "content-operator-001",
        fixed_video.stat().st_size,  # noqa: ASYNC240
    )
    asset = await repository.finalize_asset(
        upload.upload_id,
        "content-operator-001",
        content_hash=hashlib.sha256(fixed_video.read_bytes()).hexdigest(),  # noqa: ASYNC240
        bucket="media-original",
        object_key="store/owner/fixed/original.mp4",
        mime_type="video/mp4",
        duration_ms=10_000,
        width=320,
        height=180,
        fps=25,
        video_codec="h264",
        audio_codec="aac",
    )
    request = ClipTaskCreateRequest(
        principal_id="content-operator-001",
        product_keywords=["蓝牙耳机"],
        min_clip_seconds=3,
        max_clip_seconds=8,
        max_candidates=3,
        subtitle_language="zh",
        idempotency_key="pipeline-task-001",
        run_id="run-pipeline-001",
        thread_id="thread-pipeline-001",
        trace_id="trace-pipeline-001",
    )
    task, _ = await repository.create_task(asset.id, request)
    return asset, task


@pytest.mark.asyncio
async def test_fake_asr_real_ffmpeg_pipeline_is_idempotent_and_cleans_temp(
    stage5_factory: async_sessionmaker[AsyncSession],
    stage5_settings,
    fixed_video: Path,
    fake_storage,
) -> None:
    asset, task = await prepared_task(stage5_factory, fixed_video)
    fixed_payload = fixed_video.read_bytes()  # noqa: ASYNC240
    fake_storage.objects[("media-original", asset.object_key)] = fixed_payload
    resumed: list[str] = []

    async def resume(task_id: str) -> None:
        resumed.append(task_id)

    pipeline = MediaPipeline(
        session_factory=stage5_factory,
        storage=fake_storage,
        settings=stage5_settings,
        asr=FakeASR(
            ASRResult(
                language="zh",
                segments=[ASRSegment(500, 5_500, "蓝牙耳机限时优惠值得关注")],
            )
        ),
        resume_graph=resume,
    )
    await pipeline.run(asset.id, task.id, task.run_id, task.pipeline_version)
    await pipeline.run(asset.id, task.id, task.run_id, task.pipeline_version)
    async with stage5_factory() as session:
        current = await session.get(MediaTask, task.id)
        clips = (
            await session.scalars(select(ClipCandidate).where(ClipCandidate.task_id == task.id))
        ).all()
        transcript = await session.scalar(select(Transcript).where(Transcript.task_id == task.id))
        events = (
            await session.scalars(
                select(MediaTaskEvent)
                .where(MediaTaskEvent.task_id == task.id)
                .order_by(MediaTaskEvent.sequence)
            )
        ).all()
    assert current and current.status == "SUCCEEDED"
    assert transcript and transcript.segment_count == 1
    assert clips and all(row.qc_result["passed"] for row in clips)
    assert len({row.ordinal for row in clips}) == len(clips)
    assert [row.sequence for row in events] == list(range(1, len(events) + 1))
    assert resumed == [task.id]
    workspace = stage5_settings.data_root / "temp" / "media-stage5" / "tasks" / task.id
    assert not workspace.exists()


def test_celery_media_queue_is_single_prefetch_and_limited() -> None:
    assert celery_app.conf.task_default_queue == "media"
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_soft_time_limit < celery_app.conf.task_time_limit
    assert celery_app.conf.worker_max_tasks_per_child == 5


def test_faster_whisper_uses_pinned_local_snapshot_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision = "536b0662742c02347bc0e980a01041f333bce120"
    model_path = tmp_path / revision
    model_path.mkdir()
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        (model_path / name).write_bytes(b"fixed")
    calls: list[dict[str, object]] = []

    class StubWhisperModel:
        def __init__(self, path: str, **kwargs: object) -> None:
            calls.append({"path": path, **kwargs})

        def transcribe(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return iter(()), types.SimpleNamespace(language="en")

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        types.SimpleNamespace(WhisperModel=StubWhisperModel),
    )
    provider = FasterWhisperASR("Systran/faster-whisper-small", revision, model_path, "int8", 8)
    result = provider._transcribe_sync(tmp_path / "audio.wav", "en")
    assert result == ASRResult(language="en", segments=[])
    assert calls == [
        {
            "path": str(model_path),
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": 8,
            "local_files_only": True,
        }
    ]


def test_faster_whisper_large_v3_uses_cuda_zero_float16_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
    model_path = tmp_path / f"large-v3-{revision}"
    model_path.mkdir()
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"):
        (model_path / name).write_bytes(b"fixed")
    (model_path / "manifest.json").write_text(
        '{"resolved_revision":"edaa852ec7e145841d8ffdb056a99866b5f0a478"}'
    )
    calls: list[dict[str, object]] = []

    class StubWhisperModel:
        def __init__(self, path: str, **kwargs: object) -> None:
            calls.append({"path": path, **kwargs})

        def transcribe(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return iter(()), types.SimpleNamespace(language="zh")

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        types.SimpleNamespace(WhisperModel=StubWhisperModel),
    )
    provider = FasterWhisperASR(
        "Systran/faster-whisper-large-v3",
        revision,
        model_path,
        compute_type="float16",
        device="cuda",
        device_index=0,
    )
    result = provider._transcribe_sync(tmp_path / "audio.wav", "zh")
    assert result == ASRResult(language="zh", segments=[])
    assert calls == [
        {
            "path": str(model_path),
            "device": "cuda",
            "device_index": 0,
            "compute_type": "float16",
            "cpu_threads": 8,
            "local_files_only": True,
        }
    ]


def test_large_v3_gpu_settings_parse_fixed_revision_and_single_device(tmp_path: Path) -> None:
    revision = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
    settings = Settings(
        database_url="sqlite+aiosqlite:///unused.db",
        redis_url="redis://localhost:6379/15",
        asr_model="Systran/faster-whisper-large-v3",
        asr_model_id="large-v3",
        asr_model_version=revision,
        asr_model_revision=revision,
        asr_model_path=tmp_path / f"large-v3-{revision}",
        asr_device="cuda",
        asr_device_index=0,
        asr_compute_type="float16",
    )
    assert settings.asr_model_revision == revision
    assert settings.asr_device == "cuda"
    assert settings.asr_device_index == 0
    assert settings.asr_compute_type == "float16"

    with pytest.raises(ValueError):
        Settings(
            database_url="sqlite+aiosqlite:///unused.db",
            redis_url="redis://localhost:6379/15",
            asr_model="Systran/faster-whisper-large-v3",
            asr_model_id="large-v3",
            asr_model_version=revision,
            asr_model_revision=revision,
            asr_model_path=tmp_path / f"large-v3-{revision}",
            asr_device="cuda",
            asr_device_index=1,
            asr_compute_type="float16",
        )


def test_faster_whisper_reports_structured_cuda_initialization_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
    model_path = tmp_path / f"large-v3-{revision}"
    model_path.mkdir()
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"):
        (model_path / name).write_bytes(b"fixed")
    (model_path / "manifest.json").write_text(
        '{"resolved_revision":"edaa852ec7e145841d8ffdb056a99866b5f0a478"}'
    )

    class BrokenWhisperModel:
        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            raise RuntimeError("CUDA unavailable")

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        types.SimpleNamespace(WhisperModel=BrokenWhisperModel),
    )
    provider = FasterWhisperASR(
        "Systran/faster-whisper-large-v3",
        revision,
        model_path,
        compute_type="float16",
        device="cuda",
        device_index=0,
    )
    with pytest.raises(ASRInitializationError) as caught:
        provider._transcribe_sync(tmp_path / "audio.wav", "zh")
    assert caught.value.code == "ASR_CUDA_INITIALIZATION_FAILED"
    assert caught.value.__cause__ is not None


def test_large_v3_path_remains_valid_after_exact_small_directory_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
    model_root = tmp_path / "models" / "asr"
    small_path = model_root / "models--Systran--faster-whisper-small"
    large_path = model_root / f"large-v3-{revision}"
    small_path.mkdir(parents=True)
    (small_path / "model.bin").write_bytes(b"old-small")
    large_path.mkdir()
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"):
        (large_path / name).write_bytes(b"fixed-large-v3")
    (large_path / "manifest.json").write_text(
        '{"resolved_revision":"edaa852ec7e145841d8ffdb056a99866b5f0a478"}'
    )

    (small_path / "model.bin").unlink()
    small_path.rmdir()

    loaded_paths: list[str] = []

    class StubWhisperModel:
        def __init__(self, path: str, **_kwargs: object) -> None:
            loaded_paths.append(path)

        def transcribe(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            return iter(()), types.SimpleNamespace(language="zh")

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        types.SimpleNamespace(WhisperModel=StubWhisperModel),
    )
    provider = FasterWhisperASR(
        "Systran/faster-whisper-large-v3",
        revision,
        large_path,
        compute_type="float16",
        device="cuda",
        device_index=0,
    )
    provider._transcribe_sync(tmp_path / "audio.wav", "zh")

    assert not small_path.exists()
    assert large_path.is_dir()
    assert loaded_paths == [str(large_path)]


def test_faster_whisper_rejects_missing_local_model_before_loading(
    tmp_path: Path,
) -> None:
    provider = FasterWhisperASR(
        "Systran/faster-whisper-large-v3",
        "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        tmp_path / "missing",
        compute_type="float16",
        device="cuda",
        device_index=0,
    )
    with pytest.raises(ASRInitializationError) as caught:
        provider._validate_local_model()
    assert caught.value.code == "ASR_MODEL_PATH_INVALID"


def test_worker_disposes_async_engine_between_deliveries(monkeypatch) -> None:
    runs: list[str] = []
    closes: list[str] = []

    class StubPipeline:
        async def run(
            self, asset_id: str, task_id: str, run_id: str, pipeline_version: str
        ) -> None:
            runs.append(task_id)

    async def close() -> None:
        closes.append("closed")

    monkeypatch.setattr(worker_tasks, "MediaPipeline", StubPipeline)
    monkeypatch.setattr(worker_tasks, "close_engine", close)
    asyncio.run(worker_tasks._run_pipeline("asset", "task-1", "run", "version"))
    asyncio.run(worker_tasks._run_pipeline("asset", "task-2", "run", "version"))

    assert runs == ["task-1", "task-2"]
    assert closes == ["closed", "closed"]


def test_live_video_graph_is_independent_and_has_required_nodes() -> None:
    graph = build_live_video_graph()
    names = set(graph.get_graph().nodes)
    assert {
        "validate_request",
        "resolve_principal",
        "authorize_asset",
        "inspect_asset",
        "create_pipeline_task",
        "enqueue_media_worker",
        "interrupt_while_processing",
        "resume_from_task_result",
        "validate_candidates_and_qc",
        "await_human_review",
        "finalize_result",
    }.issubset(names)
    assert "orchestrator" not in names
    assert "customer_service" not in names
    assert "operations" not in names


@pytest.mark.asyncio
async def test_cancellation_terminates_owned_media_subprocess() -> None:
    started = time.monotonic()

    async def cancelled() -> bool:
        return True

    with pytest.raises(PipelineFailure) as error:
        await _command(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            10,
            cancelled,
        )
    assert error.value.code == "CANCELLED"
    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_no_audio_pipeline_skips_asr_and_cannot_publish_unsubtitled_clip(
    stage5_factory: async_sessionmaker[AsyncSession],
    stage5_settings,
    fixed_video: Path,
    fake_storage,
    tmp_path: Path,
) -> None:
    no_audio = tmp_path / "no-audio.mp4"
    subprocess.run(  # noqa: ASYNC221
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(fixed_video),
            "-an",
            "-c:v",
            "copy",
            str(no_audio),
        ],
        check=True,
        timeout=30,
    )
    asset, task = await prepared_task(stage5_factory, no_audio)
    fake_storage.objects[("media-original", asset.object_key)] = no_audio.read_bytes()  # noqa: ASYNC240

    class ForbiddenASR:
        async def transcribe(self, *_args, **_kwargs) -> ASRResult:
            raise AssertionError("ASR must not run for media without an audio stream")

    pipeline = MediaPipeline(
        stage5_factory,
        fake_storage,
        stage5_settings,
        ForbiddenASR(),  # type: ignore[arg-type]
        resume_graph=lambda _task: asyncio.sleep(0),
    )
    with pytest.raises(PipelineFailure) as error:
        await pipeline.run(asset.id, task.id, task.run_id, task.pipeline_version)
    assert error.value.code == "QC_FAILED"
    async with stage5_factory() as session:
        transcript = await session.scalar(select(Transcript).where(Transcript.task_id == task.id))
        clips = (
            await session.scalars(select(ClipCandidate).where(ClipCandidate.task_id == task.id))
        ).all()
    assert transcript and transcript.segment_count == 0
    assert clips == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["ffmpeg", "qc"])
async def test_permanent_media_and_qc_failures_do_not_persist_candidates(
    failure_mode: str,
    stage5_factory: async_sessionmaker[AsyncSession],
    stage5_settings,
    fixed_video: Path,
    fake_storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset, task = await prepared_task(stage5_factory, fixed_video)
    fake_storage.objects[("media-original", asset.object_key)] = fixed_video.read_bytes()  # noqa: ASYNC240
    if failure_mode == "ffmpeg":

        async def fail_command(*_args, **_kwargs) -> tuple[str, str]:
            raise PipelineFailure("MEDIA_SUBPROCESS_FAILED", "controlled FFmpeg failure")

        monkeypatch.setattr("app.media.pipeline._command", fail_command)
    else:

        async def wrong_stat(*_args, **_kwargs) -> tuple[int, str | None]:
            return 0, None

        monkeypatch.setattr(fake_storage, "stat", wrong_stat)
    pipeline = MediaPipeline(
        stage5_factory,
        fake_storage,
        stage5_settings,
        FakeASR(
            ASRResult(
                language="zh",
                segments=[ASRSegment(500, 5_500, "蓝牙耳机限时优惠")],
            )
        ),
        resume_graph=lambda _task: asyncio.sleep(0),
    )
    with pytest.raises(PipelineFailure) as error:
        await pipeline.run(asset.id, task.id, task.run_id, task.pipeline_version)
    assert error.value.code == (
        "MEDIA_SUBPROCESS_FAILED" if failure_mode == "ffmpeg" else "QC_FAILED"
    )
    async with stage5_factory() as session:
        current = await session.get(MediaTask, task.id)
        count = await session.scalar(
            select(func.count(ClipCandidate.id)).where(ClipCandidate.task_id == task.id)
        )
    assert current and current.status == "FAILED"
    assert count == 0
