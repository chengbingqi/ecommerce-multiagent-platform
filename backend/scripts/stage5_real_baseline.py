"""Run the unchanged Stage 5 pipeline once and capture a development baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

PROJECT = os.getenv("COMPOSE_PROJECT_NAME", "ecommerce-multiagent-stage5")
PRINCIPAL_ID = "admin-001"
ASSET_ID = "a90113d7-b6fc-4b4a-8d05-ec71b8a9eee5"
SOURCE_TASK_ID = "88a6dfe2-2f7d-479f-a584-1bd1220cc063"
DATA_ROOT = Path(os.getenv("DATA_ROOT", ".local-data"))
ORIGINAL_PATH = DATA_ROOT / "temp/media-stage5/diagnostics/real-livestream-baseline/original.mp4"
RESULT_PATH = Path("evals/stage5/results/stage5_real_pipeline_rerun_baseline_gt_v1.json")
PIPELINE_HASH_FILES = (
    Path("backend/app/media/pipeline.py"),
    Path("backend/app/media/asr.py"),
    Path("backend/app/media/scoring.py"),
)
PARAMETERS = {
    # Frozen from SOURCE_TASK_ID before the user-authored ground truth existed.
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
    "max_candidates": 10,
    "subtitle_language": "zh",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def compose(*arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        PROJECT,
        "-f",
        "compose.yaml",
        "-f",
        "compose.dev.yaml",
        "--profile",
        "stage5",
        *arguments,
    ]


def worker_container() -> str:
    result = subprocess.run(
        compose("ps", "-q", "worker-media"),
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def memory_bytes(value: str) -> int:
    match = re.fullmatch(r"([0-9.]+)\s*([A-Za-z]+)", value.strip())
    if not match:
        return 0
    factors = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
    number, unit = match.groups()
    return int(float(number) * factors.get(unit, 0))


def sample_worker(container_id: str) -> tuple[float, int]:
    result = subprocess.run(
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.CPUPerc}}|{{.MemUsage}}",
            container_id,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode or "|" not in result.stdout:
        return 0.0, 0
    cpu, memory = result.stdout.strip().split("|", 1)
    return float(cpu.rstrip("%") or 0), memory_bytes(memory.split("/", 1)[0])


def workspace_bytes(task_id: str) -> int:
    root = DATA_ROOT / "temp/media-stage5/tasks" / task_id
    if not root.is_dir():
        return 0
    return sum(row.stat().st_size for row in root.rglob("*") if row.is_file())


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def stage_duration(events: list[dict[str, Any]], stage: str) -> float | None:
    for index, row in enumerate(events):
        if row["stage"] == stage and index + 1 < len(events):
            return round(
                (
                    parse_time(events[index + 1]["timestamp"]) - parse_time(row["timestamp"])
                ).total_seconds(),
                3,
            )
    return None


def diagnostic_boundaries(path: Path, duration_seconds: float) -> dict[str, Any]:
    scene = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-filter:v",
            "select='gt(scene,0.30)',showinfo",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    scene_points = [0.0]
    for match in re.finditer(r"pts_time:([0-9.]+)", scene.stderr):
        value = float(match.group(1))
        if 0 < value < duration_seconds and value - scene_points[-1] >= 0.5:
            scene_points.append(round(value, 3))
    scene_points.append(duration_seconds)
    silence = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-af",
            "silencedetect=noise=-40dB:d=0.25",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    starts = [float(value) for value in re.findall(r"silence_start: ([0-9.]+)", silence.stderr)]
    ends = [float(value) for value in re.findall(r"silence_end: ([0-9.]+)", silence.stderr)]
    return {
        "scene_threshold": 0.30,
        "scene_points_seconds": scene_points,
        "silence_threshold_db": -40,
        "silence_minimum_seconds": 0.25,
        "silence_intervals": [
            {"start_seconds": round(start, 3), "end_seconds": round(end, 3)}
            for start, end in zip(starts, ends, strict=False)
        ],
        "faster_whisper_vad_enabled": True,
        "faster_whisper_vad_boundaries_persisted": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()
    if RESULT_PATH.exists():
        raise RuntimeError(f"refusing to overwrite baseline: {RESULT_PATH}")
    if (
        sha256_file(ORIGINAL_PATH)
        != "c0b0c51f071eeaee40e1621715a047070cf72546db1a263a28cefd577d7f3f1d"
    ):
        raise RuntimeError("original evidence hash changed")

    run_suffix = "gt-v1-baseline-20260719"
    request_payload = {
        "principal_id": PRINCIPAL_ID,
        **PARAMETERS,
        "idempotency_key": f"stage5-real-{run_suffix}-task",
        "run_id": f"stage5-real-{run_suffix}-run",
        "thread_id": f"stage5-real-{run_suffix}-thread",
        "trace_id": f"stage5-real-{run_suffix}-trace",
    }
    container_id = worker_container()
    peak_cpu = 0.0
    peak_memory = 0
    peak_temp = 0
    started = time.monotonic()
    with httpx.Client(timeout=30, trust_env=False) as client:
        response = client.post(f"{args.base_url}/media/{ASSET_ID}/clip-tasks", json=request_payload)
        response.raise_for_status()
        task = response.json()
        task_id = task["task_id"]
        while task["status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            if time.monotonic() - started > args.timeout:
                raise TimeoutError("real baseline task timed out")
            cpu, memory = sample_worker(container_id)
            peak_cpu = max(peak_cpu, cpu)
            peak_memory = max(peak_memory, memory)
            peak_temp = max(peak_temp, workspace_bytes(task_id))
            snapshot = client.get(f"{args.base_url}/tasks/{task_id}?principal_id={PRINCIPAL_ID}")
            snapshot.raise_for_status()
            task = snapshot.json()
        elapsed = round(time.monotonic() - started, 3)
        if task["status"] != "SUCCEEDED":
            raise RuntimeError(f"baseline task failed: {task.get('error_code')}")
        events = (
            client.get(f"{args.base_url}/tasks/{task_id}/events?principal_id={PRINCIPAL_ID}")
            .raise_for_status()
            .json()
        )
        transcript = (
            client.get(f"{args.base_url}/tasks/{task_id}/transcript?principal_id={PRINCIPAL_ID}")
            .raise_for_status()
            .json()
        )
        clips = (
            client.get(f"{args.base_url}/tasks/{task_id}/clips?principal_id={PRINCIPAL_ID}")
            .raise_for_status()
            .json()
        )
        for clip in clips:
            subtitle = client.get(
                f"{args.base_url}/media/clips/{clip['clip_id']}/download",
                params={"principal_id": PRINCIPAL_ID, "kind": "subtitle"},
            )
            subtitle.raise_for_status()
            clip["subtitle_srt"] = subtitle.text

    total_task_seconds = round(
        (parse_time(task["updated_at"]) - parse_time(task["created_at"])).total_seconds(), 3
    )
    duration_seconds = 137.926
    result = {
        "schema_version": "stage5_real_pipeline_baseline_v1",
        "captured_at": datetime.now().astimezone().isoformat(),
        "ground_truth_usage": "not_read_by_runner",
        "parameter_source_task_id": SOURCE_TASK_ID,
        "asset_id": ASSET_ID,
        "original_media_sha256": sha256_file(ORIGINAL_PATH),
        "task_id": task["task_id"],
        "run_id": task["run_id"],
        "thread_id": task["thread_id"],
        "trace_id": task["trace_id"],
        "idempotency_key": request_payload["idempotency_key"],
        "status": task["status"],
        "error_code": task.get("error_code"),
        "retry_count": task["retry_count"],
        "pipeline_version": task["pipeline_version"],
        "parameters": PARAMETERS,
        "asr": {
            "implementation": "faster-whisper==1.1.1",
            "model": "Systran/faster-whisper-small",
            "revision": "536b0662742c02347bc0e980a01041f333bce120",
            "device": "cpu",
            "compute_type": "int8",
            "language": "zh",
            "beam_size": 5,
            "vad_filter": True,
            "word_timestamps_requested": True,
            "words_persisted": False,
            "initial_prompt": None,
            "hotwords": None,
            "config_version": "baseline_unversioned",
        },
        "deepseek": {
            "called": False,
            "model": None,
            "prompt_version": None,
            "basis": "no DeepSeek import or provider in live_video/media/worker/media-mcp path",
        },
        "rules": {
            "candidate_builder": "fixed_asr_segment_padding",
            "score_version": "highlight_score_v1.0.0",
            "qc_version": "media_qc_v1.0.0",
        },
        "code_sha256": {str(path): sha256_file(path) for path in PIPELINE_HASH_FILES},
        "performance": {
            "wall_seconds": elapsed,
            "task_seconds": total_task_seconds,
            "asr_seconds": stage_duration(events, "transcribe"),
            "rtf": round(total_task_seconds / duration_seconds, 4),
            "worker_cpu_peak_percent": peak_cpu,
            "worker_memory_peak_bytes": peak_memory,
            "temp_disk_peak_bytes": peak_temp,
        },
        "boundaries": diagnostic_boundaries(ORIGINAL_PATH, duration_seconds),
        "events": events,
        "raw_asr": transcript,
        "predictions": clips,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULT_PATH.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(
        json.dumps(
            {
                "task_id": task["task_id"],
                "run_id": task["run_id"],
                "prediction_count": len(clips),
                "result": str(RESULT_PATH),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
