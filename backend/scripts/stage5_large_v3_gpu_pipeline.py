"""Run the unchanged Stage 5 pipeline in an isolated large-v3 GPU process."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import resource
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import (
    AgentRun,
    ClipCandidate,
    MediaAsset,
    MediaTask,
    MediaTaskEvent,
    Transcript,
    TranscriptSegment,
)
from app.db.session import get_sessionmaker
from app.media.constants import PIPELINE_VERSION, AssetStatus, TaskStatus
from app.media.pipeline import MediaPipeline
from app.media.storage import ObjectStorage, get_object_storage

ASSET_ID = "a90113d7-b6fc-4b4a-8d05-ec71b8a9eee5"
PRINCIPAL_ID = "admin-001"
REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
GPU_UUID = os.getenv("MEDIA_GPU_DEVICE_ID", "YOUR_GPU_DEVICE_ID_HERE")
PARAMETERS = {
    "product_keywords": [
        "809",
        "拖把",
        "802",
        "女生的頭繩",
        "811",
        "食品級保鮮盒",
        "812",
        "燒水壺",
        "813",
        "菜刀",
        "815",
        "814",
        "保鮮罩",
        "817",
        "卡皮巴拉硅膠墊",
        "816",
        "天然皂粉",
        "833",
        "海綿拖把",
    ],
    "min_clip_seconds": 6,
    "max_clip_seconds": 20,
    "max_candidates": 20,
    "subtitle_language": "zh",
}


def gpu_sample() -> dict[str, Any]:
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,pci.bus_id,memory.used,memory.free,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    rows = [row.strip() for row in completed.stdout.splitlines() if row.strip()]
    if len(rows) != 1:
        raise RuntimeError("GPU pipeline container must see exactly one GPU")
    index, gpu_uuid, pci_bus_id, memory_used, memory_free, utilization = (
        value.strip() for value in rows[0].split(",")
    )
    return {
        "index": int(index),
        "uuid": gpu_uuid,
        "pci_bus_id": pci_bus_id,
        "memory_used_mib": int(memory_used),
        "memory_free_mib": int(memory_free),
        "utilization_percent": int(utilization),
    }


async def create_controlled_task(suffix: str) -> MediaTask:
    session_factory = get_sessionmaker()
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        active = await session.scalar(
            select(func.count(MediaTask.id)).where(
                MediaTask.status.in_([TaskStatus.QUEUED.value, TaskStatus.RUNNING.value])
            )
        )
        if int(active or 0):
            raise RuntimeError("a formal media task is active")
        asset = await session.get(MediaAsset, ASSET_ID)
        if asset is None or not asset.object_bucket or not asset.object_key:
            raise RuntimeError("authorized development asset is unavailable")
        run_id = f"stage5-{suffix}-run"
        thread_id = f"stage5-{suffix}-thread"
        trace_id = f"stage5-{suffix}-trace"
        run = AgentRun(
            run_id=run_id,
            thread_id=thread_id,
            principal_id=PRINCIPAL_ID,
            agent_type="live_video",
            status="PENDING",
            intent="CREATE_CLIPS",
            input_summary={"asset_id": ASSET_ID, "evaluation": "large_v3_gpu_ab"},
            output_summary={},
            need_human=False,
            error_code=None,
            approval_id=None,
            trace_id=trace_id,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        await session.flush([run])
        task = MediaTask(
            id=str(uuid.uuid4()),
            asset_id=ASSET_ID,
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            owner_id=PRINCIPAL_ID,
            store_id=asset.store_id,
            status=TaskStatus.QUEUED.value,
            stage="create_pipeline_task",
            progress=0,
            pipeline_version=PIPELINE_VERSION,
            parameters=PARAMETERS,
            idempotency_key=f"stage5-{suffix}-task",
            error_code=None,
            retry_count=0,
            heartbeat_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        asset.status = AssetStatus.PROCESSING.value
        asset.updated_at = now
        await session.flush()
        return task


def stage_duration(events: list[MediaTaskEvent], stage: str) -> float | None:
    for index, event in enumerate(events[:-1]):
        if event.stage == stage:
            seconds = (events[index + 1].created_at - event.created_at).total_seconds()
            return float(round(seconds, 3))
    return None


async def read_object(storage: ObjectStorage, bucket: str, key: str) -> bytes:
    response = await storage.stream(bucket, key)
    try:
        return b"".join(response.stream(64 * 1024))
    finally:
        response.close()
        response.release_conn()


async def collect(task_id: str, storage: ObjectStorage) -> dict[str, Any]:
    session_factory = get_sessionmaker()
    async with session_factory() as session:
        task = await session.get(MediaTask, task_id)
        assert task is not None
        events = list(
            (
                await session.scalars(
                    select(MediaTaskEvent)
                    .where(MediaTaskEvent.task_id == task_id)
                    .order_by(MediaTaskEvent.sequence)
                )
            ).all()
        )
        transcript = await session.scalar(select(Transcript).where(Transcript.task_id == task_id))
        if transcript is None:
            raise RuntimeError("GPU pipeline transcript is missing")
        segments = list(
            (
                await session.scalars(
                    select(TranscriptSegment)
                    .where(TranscriptSegment.transcript_id == transcript.id)
                    .order_by(TranscriptSegment.ordinal)
                )
            ).all()
        )
        clips = list(
            (
                await session.scalars(
                    select(ClipCandidate)
                    .where(ClipCandidate.task_id == task_id)
                    .order_by(ClipCandidate.ordinal)
                )
            ).all()
        )
    if task.status != TaskStatus.SUCCEEDED.value:
        raise RuntimeError(f"isolated GPU pipeline ended in {task.status}")
    if not clips or not all(clip.qc_result.get("passed") for clip in clips):
        raise RuntimeError("isolated GPU pipeline did not render and pass QC for every candidate")
    predictions = []
    for clip in clips:
        assert clip.video_bucket and clip.video_object_key
        assert clip.subtitle_bucket and clip.subtitle_object_key
        assert clip.thumbnail_bucket and clip.thumbnail_object_key
        video = await read_object(storage, clip.video_bucket, clip.video_object_key)
        subtitle = await read_object(storage, clip.subtitle_bucket, clip.subtitle_object_key)
        thumbnail = await read_object(storage, clip.thumbnail_bucket, clip.thumbnail_object_key)
        predictions.append(
            {
                "clip_id": clip.id,
                "start_seconds": clip.start_ms / 1000,
                "end_seconds": clip.end_ms / 1000,
                "score": float(clip.score),
                "score_components": clip.score_components,
                "qc": clip.qc_result,
                "subtitle_srt": subtitle.decode("utf-8"),
                "video_bytes": len(video),
                "thumbnail_bytes": len(thumbnail),
            }
        )
    return {
        "task": task,
        "events": events,
        "transcript": transcript,
        "segments": segments,
        "predictions": predictions,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    expected = {
        "asr_model": "Systran/faster-whisper-large-v3",
        "asr_model_id": "large-v3",
        "asr_model_version": REVISION,
        "asr_model_revision": REVISION,
        "asr_device": "cuda",
        "asr_device_index": 0,
        "asr_compute_type": "float16",
        "media_worker_concurrency": 1,
    }
    for name, value in expected.items():
        if getattr(settings, name) != value:
            raise RuntimeError(f"isolated GPU setting mismatch: {name}")
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise RuntimeError("HF_HUB_OFFLINE must remain enabled")
    before = gpu_sample()
    if before["index"] != 0 or before["uuid"] != GPU_UUID:
        raise RuntimeError("container GPU mapping does not match the approved physical GPU")

    task = await create_controlled_task(args.suffix)
    samples: list[dict[str, Any]] = []
    cpu_percent_samples: list[float] = []
    monitoring = threading.Event()

    def monitor() -> None:
        previous_cpu = time.process_time()
        previous_wall = time.monotonic()
        while not monitoring.is_set():
            try:
                samples.append(gpu_sample())
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass
            current_cpu = time.process_time()
            current_wall = time.monotonic()
            wall_delta = current_wall - previous_wall
            if wall_delta > 0:
                cpu_percent_samples.append((current_cpu - previous_cpu) / wall_delta * 100)
            previous_cpu, previous_wall = current_cpu, current_wall
            monitoring.wait(0.2)

    worker = threading.Thread(target=monitor, daemon=True)
    worker.start()
    cpu_started = time.process_time()
    started = time.monotonic()
    try:
        await MediaPipeline(settings=settings, resume_graph=lambda _: asyncio.sleep(0)).run(
            ASSET_ID, task.id, task.run_id, task.pipeline_version
        )
    finally:
        monitoring.set()
        worker.join(timeout=2)
    wall_seconds = time.monotonic() - started
    cpu_seconds = time.process_time() - cpu_started
    after = gpu_sample()
    samples.append(after)
    collected = await collect(task.id, get_object_storage())
    current: MediaTask = collected["task"]
    events: list[MediaTaskEvent] = collected["events"]
    transcript: Transcript = collected["transcript"]
    segments: list[TranscriptSegment] = collected["segments"]
    predictions: list[dict[str, Any]] = collected["predictions"]
    asr_seconds = stage_duration(events, "transcribe")

    return {
        "schema_version": "stage5_large_v3_gpu_pipeline_v1",
        "created_at": datetime.now(UTC).isoformat(),
        "ground_truth_usage": "not_read_by_runner",
        "asset_id": ASSET_ID,
        "source_filename": "20260719_205206.mp4",
        "original_media_sha256": "c0b0c51f071eeaee40e1621715a047070cf72546db1a263a28cefd577d7f3f1d",
        "task_id": current.id,
        "run_id": current.run_id,
        "thread_id": current.thread_id,
        "trace_id": current.trace_id,
        "status": current.status,
        "pipeline_version": current.pipeline_version,
        "parameters": PARAMETERS,
        "asr": {
            "implementation": f"faster-whisper=={importlib.metadata.version('faster-whisper')}",
            "ctranslate2_version": importlib.metadata.version("ctranslate2"),
            "model": settings.asr_model,
            "model_id": settings.asr_model_id,
            "revision": settings.asr_model_revision,
            "path": str(settings.asr_model_path),
            "device": settings.asr_device,
            "device_index": settings.asr_device_index,
            "compute_type": settings.asr_compute_type,
            "local_files_only": True,
            "language": "zh",
            "beam_size": 5,
            "vad_filter": True,
            "word_timestamps": True,
        },
        "deepseek": {"called": False},
        "performance": {
            "wall_seconds": round(wall_seconds, 3),
            "task_seconds": round((current.updated_at - current.created_at).total_seconds(), 3),
            "asr_seconds": asr_seconds,
            "asr_rtf": round((asr_seconds or 0) / 137.926, 4),
            "total_rtf": round(wall_seconds / 137.926, 4),
            "rtf": round(wall_seconds / 137.926, 4),
            "cpu_seconds": round(cpu_seconds, 3),
            "cpu_peak_percent": round(max(cpu_percent_samples, default=0), 2),
            "process_max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "gpu_memory_peak_mib": max(row["memory_used_mib"] for row in samples),
            "gpu_utilization_peak_percent": max(row["utilization_percent"] for row in samples),
        },
        "gpu_before": before,
        "gpu_after": after,
        "events": [
            {
                "sequence": row.sequence,
                "event_type": row.event_type,
                "status": row.status,
                "stage": row.stage,
                "progress": row.progress,
                "timestamp": row.created_at.isoformat(),
            }
            for row in events
        ],
        "raw_asr": {
            "language": transcript.language,
            "asr_model_version": transcript.asr_model_version,
            "segments": [
                {
                    "start_seconds": row.start_ms / 1000,
                    "end_seconds": row.end_ms / 1000,
                    "text": row.raw_text or row.text,
                    "confidence": float(row.confidence) if row.confidence is not None else None,
                }
                for row in segments
            ],
        },
        "predictions": predictions,
        "checks": {
            "candidate_count": len(predictions),
            "render_pass_count": sum(row["video_bytes"] > 0 for row in predictions),
            "subtitle_pass_count": sum(bool(row["subtitle_srt"]) for row in predictions),
            "thumbnail_pass_count": sum(row["thumbnail_bytes"] > 0 for row in predictions),
            "qc_pass_count": sum(bool(row["qc"].get("passed")) for row in predictions),
            "oom": False,
            "cuda_error": False,
            "silent_cpu_fallback": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("refusing to overwrite isolated GPU pipeline evidence")
    document = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "task_id": document["task_id"],
                "candidate_count": document["checks"]["candidate_count"],
                "performance": document["performance"],
            }
        )
    )


if __name__ == "__main__":
    main()
