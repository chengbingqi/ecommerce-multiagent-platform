import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.customer_service.repository import RepositoryFailure
from app.db.models import (
    ClipCandidate,
    ClipReview,
    MediaGroundTruthAnnotation,
    MediaTask,
    Principal,
    ProductGroundTruthSegment,
    ToolCallAudit,
    Transcript,
    TranscriptSegment,
)
from app.media.repository import MediaRepository
from app.media.schemas import (
    ClipTaskCreateRequest,
    GroundTruthCreateRequest,
    GroundTruthSegmentInput,
    ReviewRequest,
    UploadCreateRequest,
)
from mcp_servers.common.contracts import CallContext
from mcp_servers.common.governance import execute_tool
from mcp_servers.common.registry import MEDIA_SPECS, MEDIA_V11_SPECS
from mcp_servers.media.schemas import MediaInput, MediaSideEffectV11Input, ProbeMediaV11Input
from mcp_servers.media.service import MediaTools


async def ready_asset(
    repository: MediaRepository,
    factory,
    principal_id: str = "content-operator-001",
    *,
    evaluation_scope: str = "human_quality",
    fixture_type: str = "user_provided",
    human_review_eligible: bool = True,
):
    async with factory() as session:
        store_id = await session.scalar(
            select(Principal.store_id).where(Principal.id == principal_id)
        )
    request = UploadCreateRequest(
        principal_id=principal_id,
        store_id=str(store_id),
        display_filename="fixed.mp4",
        idempotency_key=f"ready-{principal_id}",
        evaluation_scope=evaluation_scope,
        fixture_type=fixture_type,
        human_review_eligible=human_review_eligible,
    )
    asset = await repository.create_upload(request, 3)
    await repository.mark_uploaded(asset.upload_id, principal_id, 100)
    return await repository.finalize_asset(
        asset.upload_id,
        principal_id,
        content_hash="a" * 64,
        bucket="media-original",
        object_key=f"safe/{asset.id}.mp4",
        mime_type="video/mp4",
        duration_ms=10_000,
        width=320,
        height=180,
        fps=Decimal("25"),
        video_codec="h264",
        audio_codec="aac",
    )


def task_request(principal_id: str = "content-operator-001") -> ClipTaskCreateRequest:
    return ClipTaskCreateRequest(
        principal_id=principal_id,
        product_keywords=["耳机"],
        min_clip_seconds=3,
        max_clip_seconds=8,
        max_candidates=10,
        subtitle_language="zh",
        idempotency_key="media-task-fixed-001",
        run_id="run-media-fixed-001",
        thread_id="thread-media-fixed-001",
        trace_id="trace-media-fixed-001",
    )


@pytest.mark.asyncio
async def test_media_task_idempotency_and_concurrency(
    stage5_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = MediaRepository(stage5_factory)
    asset = await ready_asset(repository, stage5_factory)
    first, created = await repository.create_task(asset.id, task_request())
    second, repeated = await repository.create_task(asset.id, task_request())
    assert created is True
    assert repeated is False
    assert first.id == second.id
    changed = task_request().model_copy(update={"max_candidates": 9})
    with pytest.raises(RepositoryFailure) as conflict:
        await repository.create_task(asset.id, changed)
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_cross_owner_and_cross_store_are_forbidden_before_task_creation(
    stage5_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = MediaRepository(stage5_factory)
    asset = await ready_asset(repository, stage5_factory)
    with pytest.raises(RepositoryFailure) as exc:
        await repository.get_asset(asset.id, "content-operator-002")
    assert exc.value.code == "FORBIDDEN"
    async with stage5_factory() as session:
        count = await session.scalar(select(func.count(MediaTask.id)))
    assert count == 0


@pytest.mark.asyncio
async def test_cancel_and_review_state_constraints(
    stage5_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = MediaRepository(stage5_factory)
    asset = await ready_asset(repository, stage5_factory)
    task, _ = await repository.create_task(asset.id, task_request())
    cancelled = await repository.cancel(task.id, "content-operator-001")
    assert cancelled.status == "CANCELLING"
    async with stage5_factory() as session, session.begin():
        session.add(
            ClipCandidate(
                id="clip-fixed-001",
                task_id=task.id,
                asset_id=asset.id,
                ordinal=1,
                start_ms=0,
                end_ms=5_000,
                score=Decimal("0.8"),
                score_components={"total": "0.8000"},
                video_bucket="media-derived",
                video_object_key="safe/clip.mp4",
                subtitle_bucket="media-derived",
                subtitle_object_key="safe/clip.srt",
                thumbnail_bucket="media-derived",
                thumbnail_object_key="safe/clip.jpg",
                content_hashes={"video": "b" * 64},
                qc_result={"passed": True},
                review_status="PENDING",
                created_at=datetime.now(UTC),
            )
        )
    with pytest.raises(RepositoryFailure) as foreign_clip:
        await repository.get_clip("clip-fixed-001", "content-operator-002")
    assert foreign_clip.value.code == "FORBIDDEN"
    assert len(await repository.pending_review_clips("content-operator-001")) == 1
    assert await repository.pending_review_clips("content-operator-002") == []
    assert len(await repository.pending_review_clips("admin-001")) == 1
    async with stage5_factory() as session:
        review_count = await session.scalar(select(func.count(ClipReview.id)))
    assert review_count == 0
    await repository.apply_adjusted_render(
        "clip-fixed-001",
        "content-operator-001",
        start_ms=500,
        end_ms=4_500,
        derived_bucket="media-derived",
        object_base="safe/adjustments/adjusted-001",
        hashes={"video": "c" * 64, "subtitle": "d" * 64, "thumbnail": "e" * 64},
        qc={"passed": True, "adjustment_rerendered": True},
    )
    review = await repository.review_clip(
        "clip-fixed-001",
        ReviewRequest(
            principal_id="content-operator-001",
            decision="ADJUSTED",
            adjusted_start_seconds=Decimal("0.5"),
            adjusted_end_seconds=Decimal("4.5"),
            rubric={"playable": True},
        ),
    )
    assert review.status == "ADJUSTED"
    async with stage5_factory() as session:
        adjusted = await session.get(ClipCandidate, "clip-fixed-001")
        assert adjusted is not None
        assert (adjusted.start_ms, adjusted.end_ms) == (500, 4_500)
        assert adjusted.qc_result["adjustment_rerendered"] is True
        assert "/adjustments/" in str(adjusted.video_object_key)


@pytest.mark.asyncio
async def test_synthetic_fixture_is_read_only_and_excluded_from_human_queue(
    stage5_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = MediaRepository(stage5_factory)
    asset = await ready_asset(
        repository,
        stage5_factory,
        evaluation_scope="automated_pipeline",
        fixture_type="synthetic_color",
        human_review_eligible=False,
    )
    task, _ = await repository.create_task(asset.id, task_request())
    async with stage5_factory() as session, session.begin():
        session.add(
            ClipCandidate(
                id="clip-synthetic-001",
                task_id=task.id,
                asset_id=asset.id,
                ordinal=1,
                start_ms=0,
                end_ms=5_000,
                score=Decimal("0.8"),
                score_components={"total": "0.8000"},
                video_bucket="media-derived",
                video_object_key="safe/synthetic.mp4",
                subtitle_bucket="media-derived",
                subtitle_object_key="safe/synthetic.srt",
                thumbnail_bucket="media-derived",
                thumbnail_object_key="safe/synthetic.jpg",
                content_hashes={"video": "c" * 64},
                qc_result={"passed": True},
                review_status="PENDING",
                created_at=datetime.now(UTC),
            )
        )
    assert await repository.pending_review_clips("content-operator-001") == []
    automated = await repository.automated_review_clips("content-operator-001")
    assert len(automated) == 1
    assert automated[0].fixture_type == "synthetic_color"
    assert automated[0].human_review_eligible is False
    with pytest.raises(RepositoryFailure) as rejected:
        await repository.review_clip(
            "clip-synthetic-001",
            ReviewRequest(principal_id="content-operator-001", decision="APPROVED"),
        )
    assert rejected.value.code == "INVALID_ARGUMENT"
    async with stage5_factory() as session:
        review_count = await session.scalar(select(func.count(ClipReview.id)))
    assert review_count == 0


@pytest.mark.asyncio
async def test_ground_truth_is_human_authored_versioned_bounded_and_idempotent(
    stage5_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = MediaRepository(stage5_factory)
    asset = await ready_asset(repository, stage5_factory)
    task, _ = await repository.create_task(asset.id, task_request())
    created = datetime.now(UTC)
    async with stage5_factory() as session, session.begin():
        stored_task = await session.get(MediaTask, task.id)
        assert stored_task is not None
        stored_task.status = "SUCCEEDED"
        transcript = Transcript(
            id="transcript-ground-truth-001",
            task_id=task.id,
            asset_id=asset.id,
            language="zh",
            asr_model_version="faster-whisper-small-fixed",
            text_hash="d" * 64,
            segment_count=2,
            evidence_ref="transcript://ground-truth-source",
            created_at=created,
        )
        session.add(transcript)
        session.add_all(
            [
                TranscriptSegment(
                    id="asr-segment-ground-truth-001",
                    transcript_id=transcript.id,
                    ordinal=1,
                    start_ms=500,
                    end_ms=2_000,
                    text="原始错误文字一",
                ),
                TranscriptSegment(
                    id="asr-segment-ground-truth-002",
                    transcript_id=transcript.id,
                    ordinal=2,
                    start_ms=2_500,
                    end_ms=5_000,
                    text="原始错误文字二",
                ),
            ]
        )

    context = await repository.ground_truth_context(asset.id, "content-operator-001")
    assert context.asset.asset_id == asset.id
    assert context.annotation_versions == []
    assert [row.text for row in context.raw_asr_segments] == [
        "原始错误文字一",
        "原始错误文字二",
    ]
    assert all(row.confidence is None for row in context.raw_asr_segments)

    request = GroundTruthCreateRequest(
        principal_id="content-operator-001",
        annotation_version="gt-v1",
        source_authorized=True,
        segments=[
            GroundTruthSegmentInput(
                product_segment_id="product-01-fixed",
                product_name="蓝牙耳机",
                start_seconds=Decimal("0.500"),
                end_seconds=Decimal("3.000"),
                corrected_transcript="这是人工核对后的第一款商品口播。",
            ),
            GroundTruthSegmentInput(
                product_segment_id="product-02-fixed",
                product_name="充电器",
                start_seconds=Decimal("3.000"),
                end_seconds=Decimal("6.250"),
                corrected_transcript="这是人工核对后的第二款商品口播。",
            ),
        ],
    )
    saved = await repository.create_ground_truth(asset.id, request)
    repeated = await repository.create_ground_truth(asset.id, request)
    assert saved.annotation_id == repeated.annotation_id
    assert saved.segment_count == 2
    assert saved.segments[0].annotator == "content-operator-001"
    assert saved.segments[0].source_authorized is True

    exported = await repository.ground_truth_export(asset.id, "gt-v1", "content-operator-001")
    assert exported.schema_version == "stage5_product_ground_truth_v1"
    assert exported.source_transcript_id == "transcript-ground-truth-001"
    assert [row.product_name for row in exported.segments] == ["蓝牙耳机", "充电器"]

    changed = request.model_copy(
        update={
            "segments": [
                request.segments[0].model_copy(update={"corrected_transcript": "不同的人工内容"}),
                request.segments[1],
            ]
        }
    )
    with pytest.raises(RepositoryFailure) as conflict:
        await repository.create_ground_truth(asset.id, changed)
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"

    out_of_bounds = request.model_copy(
        update={
            "annotation_version": "gt-v2",
            "segments": [
                request.segments[0],
                request.segments[1].model_copy(update={"end_seconds": Decimal("10.001")}),
            ],
        }
    )
    with pytest.raises(RepositoryFailure) as bounds:
        await repository.create_ground_truth(asset.id, out_of_bounds)
    assert bounds.value.code == "INVALID_ARGUMENT"

    with pytest.raises(RepositoryFailure) as forbidden:
        await repository.ground_truth_context(asset.id, "content-operator-002")
    assert forbidden.value.code == "FORBIDDEN"
    async with stage5_factory() as session:
        annotation_count = await session.scalar(select(func.count(MediaGroundTruthAnnotation.id)))
        segment_count = await session.scalar(select(func.count(ProductGroundTruthSegment.id)))
    assert annotation_count == 1
    assert segment_count == 2


@pytest.mark.asyncio
async def test_synthetic_fixture_cannot_be_used_as_ground_truth(
    stage5_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = MediaRepository(stage5_factory)
    asset = await ready_asset(
        repository,
        stage5_factory,
        evaluation_scope="automated_pipeline",
        fixture_type="synthetic_color",
        human_review_eligible=False,
    )
    with pytest.raises(RepositoryFailure) as rejected:
        await repository.ground_truth_context(asset.id, "content-operator-001")
    assert rejected.value.code == "INVALID_ARGUMENT"


def test_ground_truth_schema_rejects_overlap_and_submillisecond_boundaries() -> None:
    with pytest.raises(ValueError, match="ordered and non-overlapping"):
        GroundTruthCreateRequest(
            principal_id="content-operator-001",
            annotation_version="gt-v1",
            source_authorized=True,
            segments=[
                GroundTruthSegmentInput(
                    product_segment_id="product-01-fixed",
                    product_name="商品一",
                    start_seconds=Decimal("0"),
                    end_seconds=Decimal("2"),
                    corrected_transcript="第一段人工真值",
                ),
                GroundTruthSegmentInput(
                    product_segment_id="product-02-fixed",
                    product_name="商品二",
                    start_seconds=Decimal("1.999"),
                    end_seconds=Decimal("3"),
                    corrected_transcript="第二段人工真值",
                ),
            ],
        )
    with pytest.raises(ValueError, match="millisecond precision"):
        GroundTruthSegmentInput(
            product_segment_id="product-03-fixed",
            product_name="商品三",
            start_seconds=Decimal("0.0001"),
            end_seconds=Decimal("1"),
            corrected_transcript="第三段人工真值",
        )


@pytest.mark.asyncio
async def test_media_mcp_v11_contract_audit_and_legacy_compatibility(
    stage5_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = MediaRepository(stage5_factory)
    asset = await ready_asset(repository, stage5_factory)
    task, _ = await repository.create_task(asset.id, task_request())
    tools = MediaTools(stage5_factory)
    context = CallContext(
        principal_id="content-operator-001",
        agent="live_video",
        run_id=task.run_id,
        task_id="mcp-media-call-001",
        trace_id=task.trace_id,
    )
    probe = await tools.probe_media_v11(ProbeMediaV11Input(context=context, asset_id=asset.id))
    assert probe.ok is True
    assert probe.meta.tool_version == "1.1.0"
    save = await tools.task_tool_v11(
        "save_asset",
        MediaSideEffectV11Input(
            context=context,
            asset_id=asset.id,
            media_task_id=task.id,
            idempotency_key="mcp-save-fixed-001",
        ),
    )
    assert save.ok is True
    legacy_context = context.model_copy(update={"principal_id": "agent-media"})
    legacy = await tools.feature_not_enabled(
        "save_asset", MediaInput(context=legacy_context, media_id="media-fixture-001")
    )
    assert legacy.ok is False
    assert legacy.error and legacy.error.error_code == "FEATURE_NOT_ENABLED"
    async with stage5_factory() as session:
        audits = (
            await session.scalars(
                select(ToolCallAudit).where(ToolCallAudit.trace_id == task.trace_id)
            )
        ).all()
    assert len(audits) == 3
    assert all("idempotency_key" not in row.input_summary for row in audits)
    assert {row.tool_version for row in audits} == {"1.0.0", "1.1.0"}


def test_media_schema_versions_are_explicit_and_legacy_is_unchanged() -> None:
    assert all(spec.tool_version == "1.0.0" for spec in MEDIA_SPECS.values())
    assert all(spec.tool_version == "1.1.0" for spec in MEDIA_V11_SPECS.values())
    assert MEDIA_V11_SPECS["render_clip"].side_effect is True
    assert MEDIA_V11_SPECS["save_asset"].side_effect is True
    assert MEDIA_V11_SPECS["probe_media"].side_effect is False


@pytest.mark.asyncio
async def test_media_mcp_timeout_is_structured_retryable_and_audited(
    stage5_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = CallContext(
        principal_id="content-operator-001",
        agent="live_video",
        run_id="run-media-timeout-001",
        task_id="task-media-timeout-001",
        trace_id="trace-media-timeout-001",
    )
    spec = MEDIA_V11_SPECS["submit_transcription"].model_copy(update={"timeout_seconds": 0.01})

    async def slow_handler(_session, _principal):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.1)
        return {}, []

    result = await execute_tool(
        stage5_factory,
        spec,
        context,
        {"asset_id": "asset-timeout", "media_task_id": "task-timeout"},
        slow_handler,
    )
    assert result.ok is False
    assert result.error and result.error.error_code == "TIMEOUT"
    assert result.error.retryable is True
    async with stage5_factory() as session:
        audit = await session.scalar(
            select(ToolCallAudit).where(ToolCallAudit.trace_id == context.trace_id)
        )
    assert audit and audit.error_code == "TIMEOUT"
