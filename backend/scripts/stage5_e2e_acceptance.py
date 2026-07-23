import hashlib
import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT_URL = "http://localhost:8080"
BASE_URL = f"{ROOT_URL}/api/v1"
PRINCIPAL_ID = "admin-001"
STORE_ID = "b39e17d7-c6aa-5aaf-adfc-b84c80d0f8f1"
DATA_ROOT = Path(os.getenv("DATA_ROOT", ".local-data"))
FIXTURE_ROOT = DATA_ROOT / "temp/media-stage5/fixtures"
RESULT_PATH = Path("evals/stage5/results/stage5_e2e_20260719.json")
PROJECT = os.getenv("COMPOSE_PROJECT_NAME", "ecommerce-multiagent-stage5")
ASR_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
ACCEPTANCE_RUN = "supplement-r2"


def stream_file(path: Path):  # type: ignore[no-untyped-def]
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            yield block


def worker_container() -> str:
    result = subprocess.run(
        [
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
            "ps",
            "-q",
            "worker-media",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def memory_bytes(value: str) -> int:
    match = re.fullmatch(r"([0-9.]+)\s*([A-Za-z]+)", value.strip())
    if not match:
        raise ValueError("unsupported Docker memory value")
    number, unit = match.groups()
    factors = {
        "B": 1,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
    }
    return int(float(number) * factors[unit])


def sample_worker(container_id: str) -> tuple[float, int, str]:
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", container_id],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode or "|" not in result.stdout:
        return 0.0, 0, "unavailable"
    cpu, memory = result.stdout.strip().split("|", 1)
    used = memory.split("/", 1)[0].strip()
    try:
        used_bytes = memory_bytes(used)
    except (KeyError, ValueError):
        used_bytes = 0
    return float(cpu.rstrip("%") or 0), used_bytes, memory


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def asr_seconds(events: list[dict[str, Any]]) -> float | None:
    transcribe = next((row for row in events if row["stage"] == "transcribe"), None)
    following = next(
        (
            row
            for row in events
            if transcribe
            and parse_timestamp(row["timestamp"]) > parse_timestamp(transcribe["timestamp"])
            and row["stage"] != "transcribe"
        ),
        None,
    )
    if not transcribe or not following:
        return None
    duration = parse_timestamp(following["timestamp"]) - parse_timestamp(transcribe["timestamp"])
    return round(
        duration.total_seconds(),
        3,
    )


def task_temp_bytes(task_id: str) -> int:
    path = DATA_ROOT / "temp/media-stage5/tasks" / task_id
    if not path.is_dir():
        return 0
    return sum(row.stat().st_size for row in path.rglob("*") if row.is_file())


def main() -> None:
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    container_id = worker_container()
    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=120, trust_env=False) as client:
        for index, fixture in enumerate(manifest["fixtures"], start=1):
            case_id = f"S5-E2E-{index:02}"
            path = FIXTURE_ROOT / fixture["filename"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != fixture["sha256"]:
                raise RuntimeError(f"fixture hash mismatch: {case_id}")
            upload_key = f"stage5-e2e-{ACCEPTANCE_RUN}-upload-{index:02}"
            create = client.post(
                f"{BASE_URL}/media/uploads",
                json={
                    "principal_id": PRINCIPAL_ID,
                    "store_id": STORE_ID,
                    "display_filename": fixture["filename"],
                    "idempotency_key": upload_key,
                    "evaluation_scope": "automated_pipeline",
                    "fixture_type": "synthetic_color",
                    "human_review_eligible": False,
                },
            )
            create.raise_for_status()
            upload = create.json()
            asset_id = upload["asset_id"]
            if upload["status"] not in {"READY", "COMPLETED"}:
                received = client.put(
                    f"{ROOT_URL}{upload['upload_path']}?principal_id={PRINCIPAL_ID}",
                    content=stream_file(path),
                    headers={"Content-Type": "video/mp4"},
                )
                received.raise_for_status()
                completed = client.post(
                    f"{BASE_URL}/media/uploads/{upload['upload_id']}/complete?principal_id={PRINCIPAL_ID}"
                )
                completed.raise_for_status()
                asset_id = completed.json()["asset_id"]
            task_response = client.post(
                f"{BASE_URL}/media/{asset_id}/clip-tasks",
                json={
                    "principal_id": PRINCIPAL_ID,
                    "product_keywords": fixture["keywords"],
                    "min_clip_seconds": 3,
                    "max_clip_seconds": 10,
                    "max_candidates": 2,
                    "subtitle_language": "en",
                    "idempotency_key": f"stage5-e2e-{ACCEPTANCE_RUN}-task-{index:02}",
                    "run_id": f"stage5-e2e-{ACCEPTANCE_RUN}-run-{index:02}",
                    "thread_id": f"stage5-e2e-{ACCEPTANCE_RUN}-thread-{index:02}",
                    "trace_id": f"stage5-e2e-{ACCEPTANCE_RUN}-trace-{index:02}",
                },
            )
            task_response.raise_for_status()
            task = task_response.json()
            started = time.monotonic()
            peak_cpu = 0.0
            peak_memory_bytes = 0
            peak_memory = "unavailable"
            peak_temp = 0
            while task["status"] not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                if time.monotonic() - started > 1800:
                    raise TimeoutError(f"task acceptance timeout: {case_id}")
                time.sleep(2)
                cpu, used_memory_bytes, memory = sample_worker(container_id)
                peak_cpu = max(peak_cpu, cpu)
                if used_memory_bytes >= peak_memory_bytes:
                    peak_memory_bytes = used_memory_bytes
                    peak_memory = memory
                peak_temp = max(peak_temp, task_temp_bytes(task["task_id"]))
                snapshot = client.get(
                    f"{BASE_URL}/tasks/{task['task_id']}?principal_id={PRINCIPAL_ID}"
                )
                snapshot.raise_for_status()
                task = snapshot.json()
            elapsed = round(time.monotonic() - started, 3)
            events_response = client.get(
                f"{BASE_URL}/tasks/{task['task_id']}/events?principal_id={PRINCIPAL_ID}"
            )
            events_response.raise_for_status()
            events = events_response.json()
            total_task_seconds = round(
                (
                    parse_timestamp(task["updated_at"]) - parse_timestamp(task["created_at"])
                ).total_seconds(),
                3,
            )
            transcript_keyword = False
            candidates = []
            if task["status"] == "SUCCEEDED":
                transcript = client.get(
                    f"{BASE_URL}/tasks/{task['task_id']}/transcript?principal_id={PRINCIPAL_ID}"
                )
                transcript.raise_for_status()
                words = " ".join(row["text"] for row in transcript.json()["segments"]).casefold()
                transcript_keyword = any(
                    keyword.casefold() in words for keyword in fixture["keywords"]
                )
                clips = client.get(
                    f"{BASE_URL}/tasks/{task['task_id']}/clips?principal_id={PRINCIPAL_ID}"
                )
                clips.raise_for_status()
                candidates = clips.json()
            rows.append(
                {
                    "case_id": case_id,
                    "fixture_id": fixture["fixture_id"],
                    "fixture_sha256": fixture["sha256"],
                    "evaluation_scope": "automated_pipeline",
                    "fixture_type": "synthetic_color",
                    "human_review_eligible": False,
                    "asset_id": asset_id,
                    "task_id": task["task_id"],
                    "media_task_id": task["task_id"],
                    "run_id": task["run_id"],
                    "status": task["status"],
                    "error_code": task.get("error_code"),
                    "processing_seconds": elapsed,
                    "total_task_seconds": total_task_seconds,
                    "video_seconds": fixture["duration_seconds"],
                    "asr_seconds": asr_seconds(events),
                    "real_time_factor": round(total_task_seconds / fixture["duration_seconds"], 4),
                    "worker_cpu_peak_percent": peak_cpu,
                    "worker_memory_last_sample": peak_memory,
                    "worker_memory_peak_bytes": peak_memory_bytes,
                    "temp_disk_peak_bytes": peak_temp,
                    "candidate_count": len(candidates),
                    "automatic_qc_passed": bool(candidates)
                    and all(bool(row["qc"].get("passed")) for row in candidates),
                    "asr_keyword_recalled": transcript_keyword,
                    "ffmpeg_result": (
                        "passed"
                        if task["status"] == "SUCCEEDED" and candidates
                        else "failed"
                        if task.get("error_code") == "MEDIA_SUBPROCESS_FAILED"
                        else "not_completed"
                    ),
                    "retry_count": task["retry_count"],
                    "asr_model": "Systran/faster-whisper-small",
                    "asr_model_revision": ASR_REVISION,
                    "asr_device": "cpu",
                    "asr_compute_type": "int8",
                }
            )
            print(json.dumps({"case_id": case_id, "status": task["status"]}))
    succeeded = sum(row["status"] == "SUCCEEDED" for row in rows)
    result = {
        "result_version": "stage5_e2e_v2",
        "acceptance_run": ACCEPTANCE_RUN,
        "generated_at": datetime.now(UTC).isoformat(),
        "pipeline_version": "media_pipeline_v1.0.0",
        "asr_model": "Systran/faster-whisper-small",
        "asr_model_revision": ASR_REVISION,
        "asr_mode": "cpu-int8",
        "task_count": len(rows),
        "succeeded": succeeded,
        "failed": sum(row["status"] == "FAILED" for row in rows),
        "cancelled": sum(row["status"] == "CANCELLED" for row in rows),
        "success_rate": str(round(succeeded * 100 / len(rows), 2)),
        "automatic_qc_pass_rate": str(
            round(sum(row["automatic_qc_passed"] for row in rows) * 100 / len(rows), 2)
        ),
        "asr_keyword_recall_rate": str(
            round(sum(row["asr_keyword_recalled"] for row in rows) * 100 / len(rows), 2)
        ),
        "tasks": rows,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"tasks": len(rows), "succeeded": succeeded}))


if __name__ == "__main__":
    main()
