"""Collect reproducible Stage5 candidate diagnostics through the formal HTTP API."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, cast

PRODUCT_TERMS = (
    "高压锅",
    "压力锅",
    "电饭煲",
    "电饭宝",
    "拖把",
    "香薰",
    "蒸锅",
    "煎锅",
    "苹果",
    "勺子",
    "汤勺",
    "肉勺",
    "耳机",
    "连衣裙",
)


def request_json(base_url: str, path: str) -> Any:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=30) as response:  # noqa: S310
        return json.load(response)


def download(base_url: str, path: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        urllib.request.urlopen(f"{base_url}{path}", timeout=120) as response,  # noqa: S310
        target.open("wb") as output,
    ):
        while chunk := response.read(1024 * 1024):
            output.write(chunk)


def context_for(
    segments: list[dict[str, Any]], start: float, end: float, margin: float = 15.0
) -> list[dict[str, Any]]:
    return [
        row
        for row in segments
        if float(row["end_seconds"]) > max(0.0, start - margin)
        and float(row["start_seconds"]) < end + margin
    ]


def probe(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return cast(dict[str, Any], json.loads(completed.stdout))


def candidate_record(
    clip: dict[str, Any], transcript: dict[str, Any], source_asset: dict[str, Any]
) -> dict[str, Any]:
    start = float(clip["start_seconds"])
    end = float(clip["end_seconds"])
    context = context_for(transcript["segments"], start, end)
    text = clip.get("transcript_text") or ""
    terms = sorted({term for term in PRODUCT_TERMS if term in text})
    evidence = clip.get("product_evidence") or {}
    boundary = clip.get("boundary_evidence") or {}
    qc = clip.get("qc") or {}
    return {
        "candidate_id": clip["clip_id"],
        "task_id": clip["task_id"],
        "source_asset": source_asset["display_filename"],
        "asset_id": source_asset["asset_id"],
        "start_seconds": clip["start_seconds"],
        "end_seconds": clip["end_seconds"],
        "duration": clip["duration_seconds"],
        "transcript": text,
        "product_label": clip.get("product_label") or "未识别商品",
        "label_confidence": clip.get("product_confidence") or evidence.get("confidence"),
        "label_source": clip.get("product_source") or evidence.get("source") or "unknown",
        "evidence_spans": clip.get("product_evidence_spans")
        or evidence.get("evidence_spans")
        or [],
        "matched_sku": evidence.get("matched_sku"),
        "aliases": evidence.get("aliases") or [],
        "primary_product_ratio": boundary.get("primary_product_ratio"),
        "secondary_product_ratio": boundary.get("secondary_product_ratio"),
        "unknown_ratio": boundary.get("unknown_ratio"),
        "transition_ratio": boundary.get("transition_ratio"),
        "is_multi_product": bool(
            clip.get("is_multi_product")
            or boundary.get("is_multi_product")
            or clip.get("product_label") == "多商品混合"
        ),
        "complete_sentence_start": boundary.get("complete_sentence_start"),
        "complete_sentence_end": boundary.get("complete_sentence_end"),
        "burned_subtitles": bool(qc.get("burned_subtitles")),
        "render_status": "PASSED" if qc.get("passed") else "FAILED",
        "qc_status": "PASSED" if qc.get("passed") else "FAILED",
        "current_srt": None,
        "context_minus_plus_15_seconds": context,
        "adjacent_product_terms": terms,
        "label_generation_path": (
            "ProductInterval.predicted_product_label -> ClipCandidate.product_label"
        ),
        "video_url": clip.get("video_url"),
        "subtitle_url": clip.get("subtitle_url"),
    }


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "candidate_id",
        "task_id",
        "source_asset",
        "asset_id",
        "start_seconds",
        "end_seconds",
        "duration",
        "transcript",
        "product_label",
        "label_confidence",
        "label_source",
        "evidence_spans",
        "primary_product_ratio",
        "secondary_product_ratio",
        "unknown_ratio",
        "transition_ratio",
        "is_multi_product",
        "complete_sentence_start",
        "complete_sentence_end",
        "burned_subtitles",
        "render_status",
        "qc_status",
    ]
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["evidence_spans"] = json.dumps(
                row["evidence_spans"], ensure_ascii=False, sort_keys=True
            )
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--principal-id", default="admin-001")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    parser.add_argument("--task", action="append", required=True)
    parser.add_argument("--download-task")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    query = f"principal_id={args.principal_id}"
    records: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for task_id in args.task:
        task = request_json(args.base_url, f"/tasks/{task_id}?{query}")
        asset = request_json(args.base_url, f"/media/{task['asset_id']}?{query}")
        clips = request_json(args.base_url, f"/tasks/{task_id}/clips?{query}")
        transcript = request_json(args.base_url, f"/tasks/{task_id}/transcript?{query}")
        task_records = [candidate_record(clip, transcript, asset) for clip in clips]
        if task_id == args.download_task:
            for record, clip in zip(task_records, clips, strict=True):
                clip_id = clip["clip_id"]
                asset_dir = args.output_dir / f"{args.phase}_assets" / clip_id
                srt_path = asset_dir / "subtitle.srt"
                video_path = asset_dir / "clip.mp4"
                download(
                    args.base_url,
                    f"/media/clips/{clip_id}/download?{query}&kind=subtitle",
                    srt_path,
                )
                download(
                    args.base_url,
                    f"/media/clips/{clip_id}/download?{query}&kind=video",
                    video_path,
                )
                record["current_srt"] = srt_path.read_text(encoding="utf-8")
                record["video_probe"] = probe(video_path)
        records.extend(task_records)
        tasks.append(
            {
                "task": task,
                "asset": asset,
                "transcript": transcript,
                "candidates": task_records,
            }
        )
    payload = {
        "phase": args.phase,
        "tasks": tasks,
        "render_source_has_subtitle_filter": any(
            marker in Path("backend/app/media/pipeline.py").read_text(encoding="utf-8")
            for marker in ("subtitles=", "ass=filename=")
        ),
        "frontend_candidate_player": "clipDownloadUrl(clip.clip_id)",
        "records": records,
    }
    (args.output_dir / f"{args.phase}_candidates.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_csv(args.output_dir / f"{args.phase}_candidates.csv", records)


if __name__ == "__main__":
    main()
