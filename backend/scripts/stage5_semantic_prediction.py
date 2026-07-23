"""Create truth-isolated prediction evidence for Stage 5 semantic segmentation."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app.media.asr import ASRResult, ASRSegment
from app.media.pipeline import render_srt
from app.media.scoring import CandidateInput, score_candidate
from app.media.segmentation import segment_products

ASSET_ID = "a90113d7-b6fc-4b4a-8d05-ec71b8a9eee5"
DURATION_MS = 137_926


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asr-comparison", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("refusing to overwrite prediction evidence")
    source = json.loads(args.asr_comparison.read_text())
    baseline = next(row for row in source["configs"] if row["name"] == "A_current_baseline")
    segments = [
        ASRSegment(
            start_ms=round(row["start_seconds"] * 1000),
            end_ms=round(row["end_seconds"] * 1000),
            text=row["text"],
            confidence=Decimal(str(row.get("average_log_probability", 0))).exp(),
        )
        for row in baseline["segments"]
    ]
    transcript = ASRResult(language="zh", segments=segments)
    intervals = segment_products(transcript, [0, DURATION_MS])
    predictions = []
    for ordinal, interval in enumerate(intervals, start=1):
        candidate = CandidateInput(
            start_ms=interval.start_ms,
            end_ms=interval.end_ms,
            text=interval.text,
            keyword_hits=0,
            has_audio=True,
            scene_complete=False,
        )
        predictions.append(
            {
                "clip_id": f"semantic-offline-{ordinal:02}",
                "start_seconds": interval.start_ms / 1000,
                "end_seconds": interval.end_ms / 1000,
                "predicted_product_label": interval.predicted_product_label,
                "score": score_candidate(candidate)["total"],
                "score_components": score_candidate(candidate),
                "qc": {"passed": None, "evaluation_mode": "pre-render_prediction_only"},
                "subtitle_srt": render_srt(transcript, interval.start_ms, interval.end_ms),
                "boundary_reason": interval.boundary_reason,
                "boundary_evidence": interval.boundary_evidence,
                "start_utterance_id": interval.start_utterance_id,
                "end_utterance_id": interval.end_utterance_id,
                "evidence_utterance_ids": interval.evidence_utterance_ids,
            }
        )
    document = {
        "schema_version": "stage5_semantic_prediction_v1",
        "created_at": datetime.now(UTC).isoformat(),
        "ground_truth_usage": "not_read_by_runner",
        "asset_id": ASSET_ID,
        "source_asr_comparison_path": str(args.asr_comparison),
        "source_asr_comparison_sha256": sha256_file(args.asr_comparison),
        "pipeline_version": "media_pipeline_v1.1.0-pre-render",
        "segmentation_version": "product_segmentation_v1.0.0",
        "performance": {"rtf": baseline["performance"]["rtf"]},
        "raw_asr": {"language": "zh", "segments": baseline["segments"]},
        "predictions": predictions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(document, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
    print(json.dumps({"prediction_count": len(predictions), "output": str(args.output)}))


if __name__ == "__main__":
    main()
