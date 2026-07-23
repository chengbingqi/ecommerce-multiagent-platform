"""Evaluate frozen Stage 5 predictions without exposing truth to the pipeline."""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

MATCH_IOU = 0.50
COMPLETE_COVERAGE = 0.70


def normalized_text(value: str) -> str:
    return "".join(
        character.casefold()
        for character in unicodedata.normalize("NFKC", value)
        if not character.isspace() and not unicodedata.category(character).startswith(("P", "S"))
    )


def edit_distance(source: str, target: str) -> int:
    previous = list(range(len(target) + 1))
    for source_index, source_character in enumerate(source, start=1):
        current = [source_index]
        for target_index, target_character in enumerate(target, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[target_index] + 1,
                    previous[target_index - 1] + (source_character != target_character),
                )
            )
        previous = current
    return previous[-1]


def intersection(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def temporal_iou(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    overlap = intersection(start_a, end_a, start_b, end_b)
    union = max(end_a, end_b) - min(start_a, start_b)
    return overlap / union if union else 0.0


CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "萬": 10000}
UNIT_ALIASES = {
    "塊": "price",
    "块": "price",
    "元": "price",
    "錢": "price",
    "钱": "price",
    "個": "count",
    "个": "count",
    "單": "count",
    "单": "count",
    "根": "count",
    "包": "count",
    "年": "duration",
    "天": "duration",
    "度": "degree",
    "°": "degree",
    "折": "discount",
}


def chinese_number(value: str) -> int | None:
    if not value:
        return None
    if all(character in CHINESE_DIGITS for character in value):
        return int("".join(str(CHINESE_DIGITS[character]) for character in value))
    total = 0
    current = 0
    for character in value:
        if character in CHINESE_DIGITS:
            current = CHINESE_DIGITS[character]
        elif character in CHINESE_UNITS:
            unit = CHINESE_UNITS[character]
            if unit == 10000:
                total = (total + current) * unit
                current = 0
            else:
                total += (current or 1) * unit
                current = 0
        else:
            return None
    return total + current


def numeric_tokens(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pattern = re.compile(
        r"(?P<number>[0-9]+(?:\.[0-9]+)?|[零〇一二两兩三四五六七八九十百千万萬]+)\s*"
        r"(?P<unit>[塊块元錢钱個个單单根包年天度°折])"
    )
    for match in pattern.finditer(unicodedata.normalize("NFKC", text)):
        raw = match.group("number")
        if raw[0].isdigit():
            value = raw
        else:
            converted = chinese_number(raw)
            if converted is None:
                continue
            value = str(converted)
        tokens.append((value, UNIT_ALIASES[match.group("unit")]))
    return tokens


def multiset_recall(expected: list[Any], actual: list[Any]) -> tuple[int, int, float | None]:
    expected_counts = Counter(expected)
    actual_counts = Counter(actual)
    hits = sum(min(count, actual_counts[value]) for value, count in expected_counts.items())
    total = sum(expected_counts.values())
    return hits, total, hits / total if total else None


def subtitle_overflow_count(srt: str, duration_seconds: float) -> int:
    def seconds(value: str) -> float:
        hours, minutes, rest = value.split(":")
        second, millis = rest.split(",")
        return int(hours) * 3600 + int(minutes) * 60 + int(second) + int(millis) / 1000

    count = 0
    for start, end in re.findall(r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})", srt):
        start_seconds = seconds(start)
        end_seconds = seconds(end)
        if (
            start_seconds < 0
            or start_seconds >= end_seconds
            or end_seconds > duration_seconds + 0.001
        ):
            count += 1
    return count


def asr_text_for_interval(raw_segments: list[dict[str, Any]], start: float, end: float) -> str:
    return " ".join(
        row["text"]
        for row in raw_segments
        if float(row["end_seconds"]) > start and float(row["start_seconds"]) < end
    )


def boundary_inside_asr(raw_segments: list[dict[str, Any]], boundary: float) -> bool:
    return any(
        float(row["start_seconds"]) + 0.001 < boundary < float(row["end_seconds"]) - 0.001
        for row in raw_segments
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    args = parser.parse_args()
    if args.output_json.exists() or args.output_csv.exists():
        raise RuntimeError("refusing to overwrite evaluation output")
    truth_document = json.loads(args.truth.read_text())
    prediction_document = json.loads(args.predictions.read_text())
    if prediction_document.get("ground_truth_usage") != "not_read_by_runner":
        raise RuntimeError("prediction evidence does not prove ground-truth isolation")
    if truth_document["asset_id"] != prediction_document["asset_id"]:
        raise RuntimeError("truth and prediction asset_id differ")

    truth = truth_document["segments"]
    predictions = prediction_document["predictions"]
    raw_asr = prediction_document["raw_asr"]["segments"]
    pairs: list[tuple[float, int, int]] = []
    for prediction_index, prediction in enumerate(predictions):
        for truth_index, expected in enumerate(truth):
            iou = temporal_iou(
                float(prediction["start_seconds"]),
                float(prediction["end_seconds"]),
                float(expected["start_seconds"]),
                float(expected["end_seconds"]),
            )
            pairs.append((iou, prediction_index, truth_index))
    matches: dict[int, tuple[int, float]] = {}
    used_truth: set[int] = set()
    for iou, prediction_index, truth_index in sorted(pairs, reverse=True):
        if iou < MATCH_IOU or prediction_index in matches or truth_index in used_truth:
            continue
        matches[prediction_index] = (truth_index, iou)
        used_truth.add(truth_index)

    prediction_rows: list[dict[str, Any]] = []
    boundary_errors: list[float] = []
    start_errors: list[float] = []
    end_errors: list[float] = []
    ious: list[float] = []
    cross_product_count = 0
    half_product_count = 0
    sentence_mid_count = 0
    subtitle_overflows = 0
    for prediction_index, prediction in enumerate(predictions):
        start = float(prediction["start_seconds"])
        end = float(prediction["end_seconds"])
        overlapping_truth = [
            expected["product_segment_id"]
            for expected in truth
            if intersection(
                start,
                end,
                float(expected["start_seconds"]),
                float(expected["end_seconds"]),
            )
            > 0
        ]
        crosses = len(overlapping_truth) > 1
        cross_product_count += crosses
        starts_mid = boundary_inside_asr(raw_asr, start)
        ends_mid = boundary_inside_asr(raw_asr, end)
        sentence_mid_count += starts_mid or ends_mid
        overflow = subtitle_overflow_count(prediction.get("subtitle_srt", ""), end - start)
        subtitle_overflows += overflow
        matched_truth_id = None
        iou = None
        start_error = None
        end_error = None
        truth_coverage = None
        half_product = False
        if prediction_index in matches:
            truth_index, matched_iou = matches[prediction_index]
            expected = truth[truth_index]
            truth_start = float(expected["start_seconds"])
            truth_end = float(expected["end_seconds"])
            matched_truth_id = expected["product_segment_id"]
            iou = matched_iou
            start_error = abs(start - truth_start)
            end_error = abs(end - truth_end)
            truth_coverage = intersection(start, end, truth_start, truth_end) / (
                truth_end - truth_start
            )
            half_product = truth_coverage < COMPLETE_COVERAGE
            half_product_count += half_product
            start_errors.append(start_error)
            end_errors.append(end_error)
            boundary_errors.extend([start_error, end_error])
            ious.append(iou)
        prediction_rows.append(
            {
                "clip_id": prediction["clip_id"],
                "start_seconds": start,
                "end_seconds": end,
                "matched_product_segment_id": matched_truth_id,
                "temporal_iou": iou,
                "start_absolute_error_seconds": start_error,
                "end_absolute_error_seconds": end_error,
                "truth_coverage": truth_coverage,
                "overlapping_product_segment_ids": overlapping_truth,
                "crosses_multiple_products": crosses,
                "only_partial_product": half_product,
                "starts_inside_asr_sentence": starts_mid,
                "ends_inside_asr_sentence": ends_mid,
                "subtitle_time_overflow_count": overflow,
                "score": prediction["score"],
                "score_components": prediction["score_components"],
                "qc": prediction["qc"],
            }
        )

    truth_rows: list[dict[str, Any]] = []
    total_edits = 0
    total_reference_characters = 0
    label_hits = 0
    all_expected_numbers: list[tuple[str, str]] = []
    all_actual_numbers: list[tuple[str, str]] = []
    for truth_index, expected in enumerate(truth):
        start = float(expected["start_seconds"])
        end = float(expected["end_seconds"])
        actual_text = asr_text_for_interval(raw_asr, start, end)
        reference_normalized = normalized_text(expected["corrected_transcript"])
        actual_normalized = normalized_text(actual_text)
        edits = edit_distance(reference_normalized, actual_normalized)
        reference_characters = len(reference_normalized)
        total_edits += edits
        total_reference_characters += reference_characters
        product_label = normalized_text(expected["product_name"])
        label_recalled = bool(product_label and product_label in actual_normalized)
        label_hits += label_recalled
        expected_numbers = numeric_tokens(expected["corrected_transcript"])
        actual_numbers = numeric_tokens(actual_text)
        all_expected_numbers.extend(expected_numbers)
        all_actual_numbers.extend(actual_numbers)
        matched = next(
            (
                (prediction_index, match_iou)
                for prediction_index, (matched_truth_index, match_iou) in matches.items()
                if matched_truth_index == truth_index
            ),
            None,
        )
        truth_rows.append(
            {
                "product_segment_id": expected["product_segment_id"],
                "product_name": expected["product_name"],
                "start_seconds": start,
                "end_seconds": end,
                "raw_asr_text": actual_text,
                "corrected_transcript": expected["corrected_transcript"],
                "cer_edits": edits,
                "cer_reference_characters": reference_characters,
                "cer": edits / reference_characters if reference_characters else None,
                "product_label_recalled": label_recalled,
                "expected_numeric_tokens": expected_numbers,
                "actual_numeric_tokens": actual_numbers,
                "matched_clip_id": predictions[matched[0]]["clip_id"] if matched else None,
                "matched_iou": matched[1] if matched else None,
            }
        )

    true_positives = len(matches)
    precision = true_positives / len(predictions) if predictions else 0.0
    recall = true_positives / len(truth) if truth else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    numeric_hits, numeric_total, numeric_recall = multiset_recall(
        all_expected_numbers, all_actual_numbers
    )
    price_hits, price_total, price_accuracy = multiset_recall(
        [token for token in all_expected_numbers if token[1] == "price"],
        [token for token in all_actual_numbers if token[1] == "price"],
    )
    both_boundary_within_two = sum(
        row["start_absolute_error_seconds"] is not None
        and row["start_absolute_error_seconds"] <= 2
        and row["end_absolute_error_seconds"] <= 2
        for row in prediction_rows
    )
    iou_above_070 = sum(value >= 0.70 for value in ious)
    summary = {
        "matching_iou_threshold": MATCH_IOU,
        "truth_count": len(truth),
        "prediction_count": len(predictions),
        "true_positive_count": true_positives,
        "interval_precision": precision,
        "interval_recall": recall,
        "interval_f1": f1,
        "start_boundary_absolute_error_median_seconds": median(start_errors)
        if start_errors
        else None,
        "end_boundary_absolute_error_median_seconds": median(end_errors) if end_errors else None,
        "both_boundaries_within_2_seconds_count": both_boundary_within_two,
        "both_boundaries_within_2_seconds_ratio": both_boundary_within_two / true_positives
        if true_positives
        else 0.0,
        "temporal_iou_at_least_070_count": iou_above_070,
        "temporal_iou_at_least_070_ratio": iou_above_070 / true_positives
        if true_positives
        else 0.0,
        "cross_product_candidate_count": cross_product_count,
        "partial_product_candidate_count": half_product_count,
        "sentence_middle_cut_candidate_count": sentence_mid_count,
        "chinese_cer": total_edits / total_reference_characters
        if total_reference_characters
        else None,
        "cer_edits": total_edits,
        "cer_reference_characters": total_reference_characters,
        "product_name_or_label_recall": label_hits / len(truth) if truth else None,
        "product_name_or_label_hits": label_hits,
        "brand_recall": None,
        "brand_recall_limitation": "ground truth has no separate brand field",
        "numeric_token_recall": numeric_recall,
        "numeric_token_hits": numeric_hits,
        "numeric_token_total": numeric_total,
        "price_numeric_accuracy": price_accuracy,
        "price_numeric_hits": price_hits,
        "price_numeric_total": price_total,
        "subtitle_time_overflow_count": subtitle_overflows,
        "rtf": prediction_document["performance"]["rtf"],
    }
    output = {
        "schema_version": "stage5_quality_evaluation_v1",
        "evaluation_only": True,
        "truth_path": str(args.truth),
        "prediction_path": str(args.predictions),
        "asset_id": truth_document["asset_id"],
        "annotation_version": truth_document["annotation_version"],
        "summary": summary,
        "truth_results": truth_rows,
        "prediction_results": prediction_rows,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    with args.output_json.open("x", encoding="utf-8") as destination:
        json.dump(output, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
    with args.output_csv.open("x", encoding="utf-8", newline="") as destination:
        writer = csv.writer(destination)
        writer.writerow(["metric", "value"])
        writer.writerows(summary.items())
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
