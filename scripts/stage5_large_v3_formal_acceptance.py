#!/usr/bin/env python3
"""Run one authorized Stage 5 upload/task acceptance against the formal worker.

The script deliberately never reads ground truth and never changes pipeline parameters.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT_URL = "http://127.0.0.1:8000"
BASE_URL = f"{ROOT_URL}/api/v1"
PRINCIPAL_ID = "admin-001"
STORE_ID = "b39e17d7-c6aa-5aaf-adfc-b84c80d0f8f1"
PROJECT = os.getenv("COMPOSE_PROJECT_NAME", "ecommerce-multiagent-stage5")
WORKER = f"{PROJECT}-worker-media-1"
TARGET_GPU_UUID = os.getenv("MEDIA_GPU_DEVICE_ID", "YOUR_GPU_DEVICE_ID_HERE")
VIDEO = Path(os.getenv("STAGE5_ACCEPTANCE_VIDEO", "YOUR_AUTHORIZED_VIDEO_HERE"))
RESULT = Path("reports/stage5_large_v3_gpu_migration/formal_worker_real_task.json")


def file_chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            yield chunk


def docker_stats() -> tuple[float, int]:
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", WORKER],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode or "|" not in result.stdout:
        return 0.0, 0
    cpu, memory = result.stdout.strip().split("|", 1)
    match = re.fullmatch(r"([0-9.]+)([A-Za-z]+)", memory.split("/", 1)[0].strip())
    if match is None:
        raise ValueError("unsupported Docker memory value")
    used, unit = match.groups()
    factors = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
    return float(cpu.rstrip("%")), int(float(used) * factors[unit])


def gpu_rows() -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,memory.used,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    rows = []
    for line in result.stdout.splitlines():
        index, uuid, memory, utilization = [part.strip() for part in line.split(",")]
        rows.append(
            {
                "index": int(index),
                "uuid": uuid,
                "memory_used_mib": int(memory),
                "utilization_percent": int(utilization),
            }
        )
    return rows


def monitor(stop: threading.Event, samples: list[dict[str, Any]]) -> None:
    while not stop.wait(0.5):
        try:
            cpu, memory = docker_stats()
            samples.append(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "worker_cpu_percent": cpu,
                    "worker_memory_bytes": memory,
                    "gpus": gpu_rows(),
                }
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            continue


def response_size(client: httpx.Client, url: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with client.stream("GET", url) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def main() -> None:
    if not VIDEO.is_file():
        raise RuntimeError(f"authorized video missing: {VIDEO}")
    video_sha256 = hashlib.sha256(VIDEO.read_bytes()).hexdigest()
    suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    baseline_gpu = gpu_rows()
    samples: list[dict[str, Any]] = []
    stop = threading.Event()
    monitor_thread = threading.Thread(target=monitor, args=(stop, samples), daemon=True)
    timeout = httpx.Timeout(connect=10, read=1800, write=1800, pool=10)
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        create = client.post(
            f"{BASE_URL}/media/uploads",
            json={
                "principal_id": PRINCIPAL_ID,
                "store_id": STORE_ID,
                "display_filename": f"20260719_205206-large-v3-{suffix}.mp4",
                "idempotency_key": f"large-v3-formal-upload-{suffix}",
                "evaluation_scope": "human_quality",
                "fixture_type": "user_provided",
                "human_review_eligible": True,
            },
        )
        create.raise_for_status()
        upload = create.json()
        received = client.put(
            f"{ROOT_URL}{upload['upload_path']}",
            params={"principal_id": PRINCIPAL_ID},
            content=file_chunks(VIDEO),
            headers={"Content-Type": "video/mp4"},
        )
        received.raise_for_status()
        completed = client.post(
            f"{BASE_URL}/media/uploads/{upload['upload_id']}/complete",
            params={"principal_id": PRINCIPAL_ID},
        )
        completed.raise_for_status()
        asset = completed.json()
        task_response = client.post(
            f"{BASE_URL}/media/{asset['asset_id']}/clip-tasks",
            json={
                "principal_id": PRINCIPAL_ID,
                "product_keywords": [],
                "min_clip_seconds": 10,
                "max_clip_seconds": 30,
                "max_candidates": 20,
                "subtitle_language": "zh",
                "idempotency_key": f"large-v3-formal-task-{suffix}",
                "run_id": f"large-v3-formal-run-{suffix}",
                "thread_id": f"large-v3-formal-thread-{suffix}",
                "trace_id": f"large-v3-formal-trace-{suffix}",
            },
        )
        task_response.raise_for_status()
        task = task_response.json()
        started = time.monotonic()
        monitor_thread.start()
        try:
            while task["status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                if time.monotonic() - started > 1800:
                    raise TimeoutError("formal Stage 5 task exceeded 30 minutes")
                time.sleep(1)
                snapshot = client.get(
                    f"{BASE_URL}/tasks/{task['task_id']}",
                    params={"principal_id": PRINCIPAL_ID},
                )
                snapshot.raise_for_status()
                task = snapshot.json()
        finally:
            stop.set()
            monitor_thread.join(timeout=15)
        elapsed = round(time.monotonic() - started, 3)
        if task["status"] != "SUCCEEDED":
            raise RuntimeError(f"formal task failed: {task}")
        events_response = client.get(
            f"{BASE_URL}/tasks/{task['task_id']}/events",
            params={"principal_id": PRINCIPAL_ID},
        )
        events_response.raise_for_status()
        events = events_response.json()
        transcript_response = client.get(
            f"{BASE_URL}/tasks/{task['task_id']}/transcript",
            params={"principal_id": PRINCIPAL_ID},
        )
        transcript_response.raise_for_status()
        transcript = transcript_response.json()
        clips_response = client.get(
            f"{BASE_URL}/tasks/{task['task_id']}/clips",
            params={"principal_id": PRINCIPAL_ID},
        )
        clips_response.raise_for_status()
        clips = clips_response.json()
        if not clips or not all(clip["qc"].get("passed") for clip in clips):
            raise RuntimeError(
                "formal task did not produce a non-empty all-QC-passed candidate set"
            )
        artifacts: list[dict[str, Any]] = []
        for clip in clips:
            item: dict[str, Any] = {"clip_id": clip["clip_id"]}
            for kind in ("video", "subtitle", "thumbnail"):
                size, digest = response_size(
                    client,
                    f"{BASE_URL}/media/clips/{clip['clip_id']}/download"
                    f"?principal_id={PRINCIPAL_ID}&kind={kind}",
                )
                if size <= 0:
                    raise RuntimeError(f"empty {kind} artifact for {clip['clip_id']}")
                item[kind] = {"bytes": size, "sha256": digest}
            artifacts.append(item)
        sse_ids: list[str] = []
        with client.stream(
            "GET",
            f"{BASE_URL}/tasks/{task['task_id']}/events/stream",
            params={"principal_id": PRINCIPAL_ID},
        ) as response:
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise RuntimeError("SSE endpoint returned an unexpected content type")
            for line in response.iter_lines():
                if line.startswith("id:"):
                    sse_ids.append(line.split(":", 1)[1].strip())
        target_samples = [
            gpu for sample in samples for gpu in sample["gpus"] if gpu["uuid"] == TARGET_GPU_UUID
        ]
        other_samples = [
            gpu for sample in samples for gpu in sample["gpus"] if gpu["uuid"] != TARGET_GPU_UUID
        ]
        if not target_samples:
            raise RuntimeError("no target GPU telemetry was captured")
        result = {
            "generated_at": datetime.now(UTC).isoformat(),
            "ground_truth_read": False,
            "deepseek_used": False,
            "source": {
                "path": str(VIDEO),
                "bytes": VIDEO.stat().st_size,
                "sha256": video_sha256,
            },
            "upload": upload,
            "asset": asset,
            "task": task,
            "elapsed_seconds": elapsed,
            "events": events,
            "sse": {"content_type_valid": True, "event_ids": sse_ids},
            "transcript": {
                "model_version": transcript["asr_model_version"],
                "config_version": transcript.get("asr_config_version"),
                "language": transcript["language"],
                "segment_count": len(transcript["segments"]),
                "nonempty": any(segment["text"].strip() for segment in transcript["segments"]),
            },
            "candidates": {
                "count": len(clips),
                "all_qc_passed": all(clip["qc"].get("passed") for clip in clips),
                "all_urls_present": all(
                    clip.get("video_url") and clip.get("subtitle_url") and clip.get("thumbnail_url")
                    for clip in clips
                ),
            },
            "artifacts": artifacts,
            "telemetry": {
                "baseline_gpus": baseline_gpu,
                "sample_count": len(samples),
                "target_gpu_uuid": TARGET_GPU_UUID,
                "target_peak_memory_mib": max(row["memory_used_mib"] for row in target_samples),
                "target_peak_utilization_percent": max(
                    row["utilization_percent"] for row in target_samples
                ),
                "other_gpu_peak_memory_mib": max(
                    (row["memory_used_mib"] for row in other_samples), default=0
                ),
                "other_gpu_peak_utilization_percent": max(
                    (row["utilization_percent"] for row in other_samples), default=0
                ),
                "worker_peak_cpu_percent": max(
                    (row["worker_cpu_percent"] for row in samples), default=0
                ),
                "worker_peak_memory_bytes": max(
                    (row["worker_memory_bytes"] for row in samples), default=0
                ),
            },
        }
        RESULT.parent.mkdir(parents=True, exist_ok=True)
        RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "task_id": task["task_id"],
                    "status": task["status"],
                    "candidate_count": len(clips),
                    "elapsed_seconds": elapsed,
                    "target_gpu_peak_memory_mib": result["telemetry"]["target_peak_memory_mib"],
                    "target_gpu_peak_utilization_percent": result["telemetry"][
                        "target_peak_utilization_percent"
                    ],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
