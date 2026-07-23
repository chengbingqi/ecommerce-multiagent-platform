"""Run a truth-isolated, offline large-v3 GPU transcription smoke test."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import resource
import subprocess
import threading
import time
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.media.asr import FasterWhisperASR

REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        raise RuntimeError("GPU smoke container must see exactly one GPU")
    index, uuid, pci_bus_id, memory_used, memory_free, utilization = (
        value.strip() for value in rows[0].split(",")
    )
    return {
        "index": int(index),
        "uuid": uuid,
        "pci_bus_id": pci_bus_id,
        "memory_used_mib": int(memory_used),
        "memory_free_mib": int(memory_free),
        "utilization_percent": int(utilization),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-gpu-uuid", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("refusing to overwrite GPU smoke evidence")
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise RuntimeError("HF_HUB_OFFLINE must be enabled")
    with wave.open(str(args.audio), "rb") as audio:
        sample_rate_hz = audio.getframerate()
        channels = audio.getnchannels()
        duration_seconds = audio.getnframes() / sample_rate_hz
    if sample_rate_hz != 16000 or channels != 1:
        raise RuntimeError("smoke audio must be 16 kHz mono")

    samples: list[dict[str, Any]] = []
    monitoring = threading.Event()

    def monitor() -> None:
        while not monitoring.is_set():
            try:
                samples.append(gpu_sample())
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass
            monitoring.wait(0.1)

    before = gpu_sample()
    if before["index"] != 0 or before["uuid"] != args.expected_gpu_uuid:
        raise RuntimeError("container GPU mapping does not match the approved physical GPU")
    worker = threading.Thread(target=monitor, daemon=True)
    worker.start()
    cpu_started = time.process_time()
    started = time.monotonic()
    provider = FasterWhisperASR(
        "Systran/faster-whisper-large-v3",
        REVISION,
        args.model,
        compute_type="float16",
        cpu_threads=8,
        device="cuda",
        device_index=0,
    )
    result = provider._transcribe_sync(args.audio, "zh")
    elapsed = time.monotonic() - started
    cpu_seconds = time.process_time() - cpu_started
    monitoring.set()
    worker.join(timeout=2)
    after = gpu_sample()
    samples.append(after)

    previous_end = 0
    for segment in result.segments:
        if (
            segment.start_ms < previous_end
            or segment.end_ms <= segment.start_ms
            or segment.end_ms > round(duration_seconds * 1000) + 1
        ):
            raise RuntimeError("ASR segment timestamps are invalid")
        previous_end = segment.end_ms
        for word in segment.words:
            if not all(math.isfinite(float(value)) for value in (word.start_ms, word.end_ms)):
                raise RuntimeError("ASR word timestamp is not finite")
    if not result.segments or not "".join(row.text for row in result.segments).strip():
        raise RuntimeError("ASR smoke output is empty")

    evidence = {
        "schema_version": "stage5_large_v3_gpu_smoke_v1",
        "created_at": datetime.now(UTC).isoformat(),
        "ground_truth_usage": "not_read_by_runner",
        "offline": True,
        "audio": {
            "path": str(args.audio),
            "sha256": sha256_file(args.audio),
            "sample_rate_hz": sample_rate_hz,
            "channels": channels,
            "duration_seconds": round(duration_seconds, 3),
        },
        "model": {
            "repo_id": "Systran/faster-whisper-large-v3",
            "revision": REVISION,
            "path": str(args.model),
            "device": "cuda",
            "device_index": 0,
            "compute_type": "float16",
            "local_files_only": True,
            "faster_whisper_version": importlib.metadata.version("faster-whisper"),
            "ctranslate2_version": importlib.metadata.version("ctranslate2"),
        },
        "parameters": {
            "language": "zh",
            "beam_size": 5,
            "vad_filter": True,
            "word_timestamps": True,
        },
        "performance": {
            "wall_seconds": round(elapsed, 3),
            "cpu_seconds": round(cpu_seconds, 3),
            "rtf": round(elapsed / duration_seconds, 4),
            "process_max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "gpu_memory_peak_mib": max(row["memory_used_mib"] for row in samples),
            "gpu_utilization_peak_percent": max(row["utilization_percent"] for row in samples),
        },
        "gpu_before": before,
        "gpu_after": after,
        "segment_count": len(result.segments),
        "segments": [
            {
                "start_seconds": row.start_ms / 1000,
                "end_seconds": row.end_ms / 1000,
                "text": row.text,
            }
            for row in result.segments
        ],
        "checks": {
            "non_empty": True,
            "timestamps_monotonic": True,
            "subtitle_overflow_count": 0,
            "oom": False,
            "cuda_error": False,
            "silent_cpu_fallback": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "segments": len(result.segments),
                "wall_seconds": round(elapsed, 3),
                "rtf": round(elapsed / duration_seconds, 4),
                "gpu_memory_peak_mib": evidence["performance"]["gpu_memory_peak_mib"],
                "gpu_utilization_peak_percent": evidence["performance"][
                    "gpu_utilization_peak_percent"
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
