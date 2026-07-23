"""Run Stage 5 Chinese ASR configurations without reading human ground truth."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel  # type: ignore[import-not-found]

MODEL = "Systran/faster-whisper-small"
REVISION = "536b0662742c02347bc0e980a01041f333bce120"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def transcribe(
    model: WhisperModel, audio: Path, config: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any], float, float]:
    before_cpu = time.process_time()
    started = time.monotonic()
    segments, info = model.transcribe(str(audio), **config)
    rows: list[dict[str, Any]] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        rows.append(
            {
                "segment_id": f"asr-{len(rows) + 1:04}",
                "start_seconds": round(segment.start, 3),
                "end_seconds": round(segment.end, 3),
                "text": text,
                "average_log_probability": getattr(segment, "avg_logprob", None),
                "no_speech_probability": getattr(segment, "no_speech_prob", None),
                "words": [
                    {
                        "start_seconds": round(word.start, 3),
                        "end_seconds": round(word.end, 3),
                        "text": word.word,
                        "probability": word.probability,
                    }
                    for word in (segment.words or [])
                ],
            }
        )
    wall_seconds = time.monotonic() - started
    cpu_seconds = time.process_time() - before_cpu
    return (
        rows,
        {
            "detected_language": info.language,
            "language_probability": info.language_probability,
            "duration_seconds": info.duration,
            "duration_after_vad_seconds": info.duration_after_vad,
        },
        wall_seconds,
        cpu_seconds,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--lexicon", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("refusing to overwrite ASR comparison output")
    model_path = Path(os.environ["ASR_MODEL_PATH"])
    if model_path.name != REVISION:
        raise RuntimeError("ASR model revision mismatch")
    lexicon = json.loads(args.lexicon.read_text())
    if (
        lexicon.get("schema_version") != "stage5_official_lexicon_v1"
        or lexicon.get("source") != "business-mcp"
        or lexicon.get("tool") != "query_inventory"
    ):
        raise RuntimeError("official lexicon evidence is invalid")
    model = WhisperModel(
        str(model_path),
        device="cpu",
        compute_type="int8",
        cpu_threads=int(os.getenv("ASR_CPU_THREADS", "8")),
        local_files_only=True,
    )
    current = {
        "language": "zh",
        "beam_size": 5,
        "vad_filter": True,
        "word_timestamps": True,
    }
    tuned = {
        "language": "zh",
        "beam_size": 8,
        "vad_filter": True,
        "vad_parameters": {
            "threshold": 0.45,
            "min_silence_duration_ms": 300,
            "speech_pad_ms": 200,
        },
        "word_timestamps": True,
        "condition_on_previous_text": True,
        "no_speech_threshold": 0.5,
    }
    configs = [
        ("A_current_baseline", "baseline_unversioned", current),
        (
            "B_explicit_chinese",
            "asr_config_zh_explicit_v1.0.0",
            dict(current),
        ),
        (
            "C_chinese_vad_beam",
            "asr_config_zh_v1.1.0",
            tuned,
        ),
        (
            "D_chinese_vad_beam_official_hotwords",
            "asr_config_zh_hotwords_v1.1.0",
            {
                **tuned,
                "initial_prompt": "电商直播商品介绍；只使用正式目录中的SKU代码。",
                "hotwords": ",".join(lexicon["terms"]),
            },
        ),
    ]
    duration_seconds = 137.926
    rows = []
    for name, version, config in configs:
        segments, info, wall_seconds, cpu_seconds = transcribe(model, args.audio, config)
        rows.append(
            {
                "name": name,
                "config_version": version,
                "parameters": config,
                "performance": {
                    "wall_seconds": round(wall_seconds, 3),
                    "cpu_seconds": round(cpu_seconds, 3),
                    "rtf": round(wall_seconds / duration_seconds, 4),
                    "process_max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    * 1024,
                },
                "info": info,
                "segments": segments,
            }
        )
        print(
            json.dumps(
                {"name": name, "segments": len(segments), "rtf": rows[-1]["performance"]["rtf"]}
            )
        )
    result = {
        "schema_version": "stage5_asr_comparison_v1",
        "created_at": datetime.now(UTC).isoformat(),
        "ground_truth_usage": "not_read_by_runner",
        "audio": {
            "path": str(args.audio),
            "sha256": sha256_file(args.audio),
            "sample_rate_hz": 16000,
            "channels": 1,
            "normalization": "pcm_s16le_resample_only_no_gain",
        },
        "model": {
            "repository": MODEL,
            "revision": REVISION,
            "device": "cpu",
            "compute_type": "int8",
            "local_files_only": True,
        },
        "official_lexicon": lexicon,
        "configs": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(result, destination, ensure_ascii=False, indent=2)
        destination.write("\n")


if __name__ == "__main__":
    main()
