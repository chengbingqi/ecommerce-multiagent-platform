"""Score raw ASR variants against frozen truth after transcription completes."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from evaluate_stage5_quality import (
    edit_distance,
    multiset_recall,
    normalized_text,
    numeric_tokens,
)


def interval_text(segments: list[dict[str, Any]], start: float, end: float) -> str:
    return " ".join(
        segment["text"]
        for segment in segments
        if float(segment["end_seconds"]) > start and float(segment["start_seconds"]) < end
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", required=True, type=Path)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--deepseek-correction", type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    args = parser.parse_args()
    if args.output_json.exists() or args.output_csv.exists():
        raise RuntimeError("refusing to overwrite ASR evaluation")
    truth_document = json.loads(args.truth.read_text())
    comparison = json.loads(args.comparison.read_text())
    if comparison.get("ground_truth_usage") != "not_read_by_runner":
        raise RuntimeError("ASR runner did not prove ground-truth isolation")
    truth = truth_document["segments"]
    configs = list(comparison["configs"])
    if args.deepseek_correction:
        correction = json.loads(args.deepseek_correction.read_text())
        if correction.get("ground_truth_usage") != "not_read_by_runner":
            raise RuntimeError("DeepSeek correction is not isolated from ground truth")
        asr_wall_seconds = float(configs[0]["performance"]["wall_seconds"])
        correction_wall_seconds = sum(
            float(call["latency_ms"]) / 1000 for call in correction["calls"]
        )
        configs.append(
            {
                "name": "E_best_asr_evidence_constrained_deepseek",
                "config_version": "asr_config_zh_v1.1.0+asr_correction_evidence_v1.0.0",
                "segments": [
                    {**segment, "text": segment["corrected_text"]}
                    for segment in correction["corrected_segments"]
                ],
                "performance": {
                    "rtf": round((asr_wall_seconds + correction_wall_seconds) / 137.926, 4),
                    "wall_seconds": round(asr_wall_seconds + correction_wall_seconds, 3),
                },
                "deepseek_calls": correction["calls"],
            }
        )
    rows: list[dict[str, Any]] = []
    for config in configs:
        total_edits = 0
        total_reference_characters = 0
        label_hits = 0
        expected_numbers: list[tuple[str, str]] = []
        actual_numbers: list[tuple[str, str]] = []
        cases = []
        for expected in truth:
            actual_text = interval_text(
                config["segments"],
                float(expected["start_seconds"]),
                float(expected["end_seconds"]),
            )
            reference = normalized_text(expected["corrected_transcript"])
            actual = normalized_text(actual_text)
            edits = edit_distance(reference, actual)
            total_edits += edits
            total_reference_characters += len(reference)
            label = normalized_text(expected["product_name"])
            label_recalled = bool(label and label in actual)
            label_hits += label_recalled
            case_expected_numbers = numeric_tokens(expected["corrected_transcript"])
            case_actual_numbers = numeric_tokens(actual_text)
            expected_numbers.extend(case_expected_numbers)
            actual_numbers.extend(case_actual_numbers)
            cases.append(
                {
                    "product_segment_id": expected["product_segment_id"],
                    "raw_asr_text": actual_text,
                    "corrected_transcript": expected["corrected_transcript"],
                    "cer_edits": edits,
                    "cer_reference_characters": len(reference),
                    "product_label_recalled": label_recalled,
                    "expected_numeric_tokens": case_expected_numbers,
                    "actual_numeric_tokens": case_actual_numbers,
                }
            )
        numeric_hits, numeric_total, numeric_recall = multiset_recall(
            expected_numbers, actual_numbers
        )
        price_hits, price_total, price_accuracy = multiset_recall(
            [token for token in expected_numbers if token[1] == "price"],
            [token for token in actual_numbers if token[1] == "price"],
        )
        rows.append(
            {
                "name": config["name"],
                "config_version": config["config_version"],
                "cer": total_edits / total_reference_characters,
                "cer_edits": total_edits,
                "cer_reference_characters": total_reference_characters,
                "product_name_or_label_recall": label_hits / len(truth),
                "product_name_or_label_hits": label_hits,
                "product_name_or_label_total": len(truth),
                "numeric_token_recall": numeric_recall,
                "numeric_token_hits": numeric_hits,
                "numeric_token_total": numeric_total,
                "price_numeric_accuracy": price_accuracy,
                "price_numeric_hits": price_hits,
                "price_numeric_total": price_total,
                "rtf": config["performance"]["rtf"],
                "wall_seconds": config["performance"]["wall_seconds"],
                "segment_count": len(config["segments"]),
                "cases": cases,
            }
        )
    output = {
        "schema_version": "stage5_asr_evaluation_v1",
        "evaluation_only": True,
        "truth_path": str(args.truth),
        "comparison_path": str(args.comparison),
        "asset_id": truth_document["asset_id"],
        "annotation_version": truth_document["annotation_version"],
        "results": rows,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    with args.output_json.open("x", encoding="utf-8") as destination:
        json.dump(output, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
    fieldnames = [
        "name",
        "config_version",
        "cer",
        "product_name_or_label_recall",
        "numeric_token_recall",
        "price_numeric_accuracy",
        "rtf",
        "wall_seconds",
        "segment_count",
    ]
    with args.output_csv.open("x", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(
        json.dumps(
            [
                {
                    "name": row["name"],
                    "cer": row["cer"],
                    "label_recall": row["product_name_or_label_recall"],
                    "price_accuracy": row["price_numeric_accuracy"],
                    "rtf": row["rtf"],
                }
                for row in rows
            ],
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
