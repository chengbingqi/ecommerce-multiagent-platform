"""Create the frozen Stage 5 determinism and human-review manifests."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def request_json(base_url: str, path: str) -> Any:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=30) as response:
        return json.load(response)


def interval_iou(left: dict[str, Any], right: dict[str, Any]) -> float:
    start = max(float(left["start_seconds"]), float(right["start_seconds"]))
    end = min(float(left["end_seconds"]), float(right["end_seconds"]))
    intersection = max(0.0, end - start)
    union = max(float(left["end_seconds"]), float(right["end_seconds"])) - min(
        float(left["start_seconds"]), float(right["start_seconds"])
    )
    return intersection / union if union else 1.0


def equally_spaced(records: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    if len(records) <= size:
        return records
    indices = [round(index * (len(records) - 1) / (size - 1)) for index in range(size)]
    return [records[index] for index in indices]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--first-task", required=True)
    parser.add_argument("--second-task", required=True)
    parser.add_argument("--source-asset", required=True)
    parser.add_argument("--review-set-id", required=True)
    parser.add_argument("--principal-id", default="admin-001")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    args = parser.parse_args()

    query = urllib.parse.urlencode({"principal_id": args.principal_id})
    first = request_json(args.base_url, f"/tasks/{args.first_task}/clips?{query}")
    second = request_json(args.base_url, f"/tasks/{args.second_task}/clips?{query}")
    first = sorted(first, key=lambda item: (float(item["start_seconds"]), item["clip_id"]))
    second = sorted(second, key=lambda item: (float(item["start_seconds"]), item["clip_id"]))

    unmatched = set(range(len(second)))
    matches: list[dict[str, Any]] = []
    for left in first:
        if not unmatched:
            break
        candidates = sorted(
            ((interval_iou(left, second[index]), index) for index in unmatched),
            key=lambda item: (-item[0], item[1]),
        )
        iou, index = candidates[0]
        if iou < 0.70:
            continue
        right = second[index]
        unmatched.remove(index)
        matches.append(
            {
                "first_candidate_id": left["clip_id"],
                "second_candidate_id": right["clip_id"],
                "time_iou": round(iou, 6),
                "start_delta_seconds": round(
                    abs(float(left["start_seconds"]) - float(right["start_seconds"])), 6
                ),
                "end_delta_seconds": round(
                    abs(float(left["end_seconds"]) - float(right["end_seconds"])), 6
                ),
                "first_product_label": left["product_label"],
                "second_product_label": right["product_label"],
            }
        )

    boundary_deltas = [
        value
        for match in matches
        for value in (match["start_delta_seconds"], match["end_delta_seconds"])
    ]
    matched_denominator = max(len(first), len(second), 1)
    stability = {
        "source_asset": args.source_asset,
        "pipeline_version": "media_pipeline_v1.2.1",
        "first_task_id": args.first_task,
        "second_task_id": args.second_task,
        "first_candidate_count": len(first),
        "second_candidate_count": len(second),
        "candidate_count_delta": abs(len(first) - len(second)),
        "iou_threshold": 0.70,
        "matched_candidate_count": len(matches),
        "matched_candidate_ratio": round(len(matches) / matched_denominator, 6),
        "median_boundary_delta_seconds": round(statistics.median(boundary_deltas), 6)
        if boundary_deltas
        else None,
        "count_gate_passed": len(first) == len(second),
        "iou_gate_passed": len(matches) / matched_denominator >= 0.90,
        "boundary_gate_passed": bool(boundary_deltas) and statistics.median(boundary_deltas) <= 1.0,
        "matches": matches,
    }
    (args.output_dir / "determinism_report.json").write_text(
        json.dumps(stability, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    after = json.loads((args.output_dir / "after_candidates.json").read_text(encoding="utf-8"))
    feedback_records = sorted(
        (record for record in after["records"] if record["task_id"] == args.first_task),
        key=lambda item: (float(item["start_seconds"]), item["candidate_id"]),
    )
    review_items: list[dict[str, Any]] = []
    for record in equally_spaced(feedback_records, 10):
        clip_id = record["candidate_id"]
        review_items.append(
            {
                "candidate_id": clip_id,
                "start_seconds": record["start_seconds"],
                "end_seconds": record["end_seconds"],
                "product_label": record["product_label"],
                "label_confidence": record["label_confidence"],
                "label_source": record["label_source"],
                "label_evidence": record["evidence_spans"],
                "playback_url": (
                    f"{args.base_url}/media/clips/{clip_id}/download?{query}&kind=video"
                ),
                "subtitle_download_url": (
                    f"{args.base_url}/media/clips/{clip_id}/download?{query}&kind=subtitle"
                ),
                "burned_subtitles": record["burned_subtitles"],
                "user_review_status": "PENDING",
            }
        )
    manifest = {
        "review_set_id": args.review_set_id,
        "sampling_method": "按开始时间排序后等距抽样；未按评分选择",
        "source_asset": args.source_asset,
        "task_id": args.first_task,
        "human_review_completed": 0,
        "human_review_required": len(review_items),
        "items": review_items,
    }
    (args.output_dir / "review_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    columns = [
        "review_set_id",
        "candidate_id",
        "start_seconds",
        "end_seconds",
        "product_label",
        "label_confidence",
        "label_source",
        "label_evidence",
        "playback_url",
        "subtitle_download_url",
        "burned_subtitles",
        "user_review_status",
    ]
    with (args.output_dir / "review_manifest.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for item in review_items:
            writer.writerow(
                {
                    "review_set_id": args.review_set_id,
                    **item,
                    "label_evidence": json.dumps(
                        item["label_evidence"], ensure_ascii=False, sort_keys=True
                    ),
                }
            )


if __name__ == "__main__":
    main()
