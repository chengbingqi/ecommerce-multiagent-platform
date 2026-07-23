"""Run the modified Stage 5 pipeline without reading development ground truth."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import httpx
from stage5_real_baseline import (
    ASSET_ID,
    ORIGINAL_PATH,
    PRINCIPAL_ID,
    diagnostic_boundaries,
    parse_time,
    sample_worker,
    sha256_file,
    stage_duration,
    worker_container,
    workspace_bytes,
)

RESULT_PATH = Path("evals/stage5/results/stage5_real_pipeline_modified_gt_v1.json")
PARAMETERS = {
    # Frozen before ground-truth access from the original user task input.
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()
    if RESULT_PATH.exists():
        raise RuntimeError("refusing to overwrite modified pipeline evidence")
    if (
        sha256_file(ORIGINAL_PATH)
        != "c0b0c51f071eeaee40e1621715a047070cf72546db1a263a28cefd577d7f3f1d"
    ):
        raise RuntimeError("original evidence hash changed")
    suffix = "gt-v1-semantic-v1-20260719"
    payload = {
        "principal_id": PRINCIPAL_ID,
        **PARAMETERS,
        "idempotency_key": f"stage5-real-{suffix}-task",
        "run_id": f"stage5-real-{suffix}-run",
        "thread_id": f"stage5-real-{suffix}-thread",
        "trace_id": f"stage5-real-{suffix}-trace",
    }
    container_id = worker_container()
    peak_cpu = 0.0
    peak_memory = 0
    peak_temp = 0
    started = time.monotonic()
    with httpx.Client(timeout=30, trust_env=False) as client:
        task = client.post(f"{args.base_url}/media/{ASSET_ID}/clip-tasks", json=payload)
        task.raise_for_status()
        snapshot = task.json()
        task_id = snapshot["task_id"]
        while snapshot["status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            if time.monotonic() - started > args.timeout:
                raise TimeoutError("modified task timed out")
            cpu, memory = sample_worker(container_id)
            peak_cpu = max(peak_cpu, cpu)
            peak_memory = max(peak_memory, memory)
            peak_temp = max(peak_temp, workspace_bytes(task_id))
            time.sleep(0.5)
            response = client.get(
                f"{args.base_url}/tasks/{task_id}", params={"principal_id": PRINCIPAL_ID}
            )
            response.raise_for_status()
            snapshot = response.json()
        if snapshot["status"] != "SUCCEEDED":
            raise RuntimeError(f"modified pipeline failed: {snapshot.get('error_code')}")
        events_response = client.get(
            f"{args.base_url}/tasks/{task_id}/events", params={"principal_id": PRINCIPAL_ID}
        )
        events_response.raise_for_status()
        events = events_response.json()
        transcript_response = client.get(
            f"{args.base_url}/tasks/{task_id}/transcript", params={"principal_id": PRINCIPAL_ID}
        )
        transcript_response.raise_for_status()
        transcript = transcript_response.json()
        clips_response = client.get(
            f"{args.base_url}/tasks/{task_id}/clips", params={"principal_id": PRINCIPAL_ID}
        )
        clips_response.raise_for_status()
        clips = clips_response.json()
        for clip in clips:
            subtitle = client.get(
                f"{args.base_url}/media/clips/{clip['clip_id']}/download",
                params={"principal_id": PRINCIPAL_ID, "kind": "subtitle"},
            )
            subtitle.raise_for_status()
            clip["subtitle_srt"] = subtitle.text
    task_seconds = round(
        (parse_time(snapshot["updated_at"]) - parse_time(snapshot["created_at"])).total_seconds(), 3
    )
    result = {
        "schema_version": "stage5_real_pipeline_modified_v1",
        "captured_at": datetime.now().astimezone().isoformat(),
        "ground_truth_usage": "not_read_by_runner",
        "asset_id": ASSET_ID,
        "original_media_sha256": sha256_file(ORIGINAL_PATH),
        "task_id": task_id,
        "run_id": snapshot["run_id"],
        "thread_id": snapshot["thread_id"],
        "trace_id": snapshot["trace_id"],
        "idempotency_key": payload["idempotency_key"],
        "status": snapshot["status"],
        "pipeline_version": snapshot["pipeline_version"],
        "parameters": PARAMETERS,
        "asr": {
            "implementation": "faster-whisper==1.1.1",
            "model": "Systran/faster-whisper-small",
            "revision": "536b0662742c02347bc0e980a01041f333bce120",
            "device": "cpu",
            "compute_type": "int8",
            "local_files_only": True,
            "config_version": "asr_config_zh_v1.1.0",
            "language": "zh",
            "beam_size": 5,
            "vad_filter": True,
            "word_timestamps": True,
        },
        "deepseek": {
            "called": False,
            "reason": "evidence-constrained E evaluation fell back for all segments",
        },
        "rules": {"segmentation_version": "product_segmentation_v1.0.0", "fixed_duration": False},
        "performance": {
            "wall_seconds": round(time.monotonic() - started, 3),
            "task_seconds": task_seconds,
            "asr_seconds": stage_duration(events, "transcribe"),
            "rtf": round(task_seconds / 137.926, 4),
            "worker_cpu_peak_percent": peak_cpu,
            "worker_memory_peak_bytes": peak_memory,
            "temp_disk_peak_bytes": peak_temp,
        },
        "boundaries": diagnostic_boundaries(ORIGINAL_PATH, 137.926),
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
            {"task_id": task_id, "run_id": snapshot["run_id"], "prediction_count": len(clips)}
        )
    )


if __name__ == "__main__":
    main()
