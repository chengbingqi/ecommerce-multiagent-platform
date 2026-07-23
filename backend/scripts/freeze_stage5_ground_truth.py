"""Freeze a user-authored Stage 5 ground-truth export without rewriting it.

This command is intentionally evaluation-only: it validates the immutable API
export, binds it to the original media hash, writes with exclusive creation, and
makes the frozen files read-only.  It never feeds annotations into the pipeline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_json(url: str, timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(f"ground-truth API returned HTTP {response.status}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("ground-truth API returned a non-object payload")
    return payload


def validate_export(
    export: dict[str, Any],
    *,
    asset: dict[str, Any],
    expected_asset_id: str,
    expected_version: str,
    expected_annotator: str,
) -> None:
    errors: list[str] = []
    if export.get("schema_version") != "stage5_product_ground_truth_v1":
        errors.append("unsupported schema_version")
    if export.get("asset_id") != expected_asset_id:
        errors.append("asset_id mismatch")
    if export.get("annotation_version") != expected_version:
        errors.append("annotation_version mismatch")
    if export.get("annotator") != expected_annotator:
        errors.append("annotator mismatch")
    if export.get("source_authorized") is not True:
        errors.append("source_authorized must be true")
    if not export.get("created_at"):
        errors.append("created_at is missing")
    if not export.get("source_transcript_id"):
        errors.append("source_transcript_id is missing")
    if asset.get("asset_id") != expected_asset_id:
        errors.append("asset context mismatch")
    if asset.get("owner_id") != expected_annotator:
        errors.append("annotator does not match the asset owner")
    if asset.get("fixture_type") != "user_provided":
        errors.append("asset is not user_provided")
    if asset.get("human_review_eligible") is not True:
        errors.append("asset is not human-review eligible")
    duration = Decimal(str(asset.get("duration_seconds")))
    segments = export.get("segments")
    if not isinstance(segments, list) or len(segments) < 10:
        errors.append("at least 10 segments are required")
        segments = []
    seen_ids: set[str] = set()
    previous_end = Decimal("0")
    for ordinal, segment in enumerate(segments, start=1):
        segment_id = str(segment.get("product_segment_id", ""))
        if not segment_id or segment_id in seen_ids:
            errors.append(f"segment {ordinal} has a missing or duplicate id")
        seen_ids.add(segment_id)
        if segment.get("asset_id") != expected_asset_id:
            errors.append(f"segment {ordinal} asset_id mismatch")
        if segment.get("annotation_version") != expected_version:
            errors.append(f"segment {ordinal} annotation_version mismatch")
        if segment.get("annotator") != expected_annotator:
            errors.append(f"segment {ordinal} annotator mismatch")
        if segment.get("source_authorized") is not True:
            errors.append(f"segment {ordinal} is not source-authorized")
        if not str(segment.get("product_name", "")).strip():
            errors.append(f"segment {ordinal} product_name is blank")
        if not str(segment.get("corrected_transcript", "")).strip():
            errors.append(f"segment {ordinal} corrected_transcript is blank")
        try:
            start = Decimal(str(segment["start_seconds"]))
            end = Decimal(str(segment["end_seconds"]))
        except (KeyError, ValueError):
            errors.append(f"segment {ordinal} timestamps are invalid")
            continue
        if start < 0 or start >= end or end > duration:
            errors.append(f"segment {ordinal} timestamps are out of bounds")
        if ordinal > 1 and start < previous_end:
            errors.append(f"segment {ordinal} overlaps the previous segment")
        previous_end = end
    if errors:
        raise RuntimeError("invalid ground truth: " + "; ".join(errors))


def write_exclusive(path: Path, payload: bytes) -> None:
    with path.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    path.chmod(0o444)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--annotation-version", required=True)
    parser.add_argument("--annotator", required=True)
    parser.add_argument("--principal-id", required=True)
    parser.add_argument("--original", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--api-base", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    query = urllib.parse.urlencode({"principal_id": args.principal_id})
    export_url = (
        f"{args.api_base}/media/{args.asset_id}/ground-truth/"
        f"{urllib.parse.quote(args.annotation_version, safe='')}/export?{query}"
    )
    context_url = f"{args.api_base}/media/{args.asset_id}/ground-truth/context?{query}"
    export = fetch_json(export_url, args.timeout)
    context = fetch_json(context_url, args.timeout)
    asset = context.get("asset")
    if not isinstance(asset, dict):
        raise RuntimeError("ground-truth context is missing its asset")
    validate_export(
        export,
        asset=asset,
        expected_asset_id=args.asset_id,
        expected_version=args.annotation_version,
        expected_annotator=args.annotator,
    )
    if not args.original.is_file() or args.original.is_symlink():
        raise RuntimeError("original media evidence is unavailable or unsafe")
    original_sha256 = sha256_file(args.original)
    if original_sha256 != asset.get("content_hash"):
        raise RuntimeError("original media hash does not match MediaAsset")

    export_bytes = (
        json.dumps(export, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    annotation_sha256 = hashlib.sha256(export_bytes).hexdigest()
    output_dir = args.output_root / args.asset_id / args.annotation_version
    annotation_path = output_dir / "annotation.json"
    manifest_path = output_dir / "manifest.json"
    if output_dir.exists():
        if not annotation_path.is_file() or not manifest_path.is_file():
            raise RuntimeError("freeze directory exists but is incomplete")
        if sha256_file(annotation_path) != annotation_sha256:
            raise RuntimeError("existing frozen annotation differs from API export")
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("annotation_sha256") != annotation_sha256:
            raise RuntimeError("existing frozen manifest hash differs")
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        return

    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": "stage5_ground_truth_manifest_v1",
        "evaluation_usage": "metrics_only",
        "prohibited_pipeline_inputs": [
            "start_seconds",
            "end_seconds",
            "corrected_transcript",
            "product_name",
        ],
        "asset_id": args.asset_id,
        "annotation_version": args.annotation_version,
        "annotation_created_at": export["created_at"],
        "frozen_at": datetime.now(UTC).isoformat(),
        "annotator": args.annotator,
        "source_authorized": True,
        "source_transcript_id": export["source_transcript_id"],
        "sample_count": len(export["segments"]),
        "annotation_sha256": annotation_sha256,
        "original_media_sha256": original_sha256,
        "original_duration_seconds": asset["duration_seconds"],
        "annotation_path": str(annotation_path.resolve()),
        "original_evidence_path": str(args.original.resolve()),
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    try:
        write_exclusive(annotation_path, export_bytes)
        write_exclusive(manifest_path, manifest_bytes)
        output_dir.chmod(0o555)
    except Exception:
        # Do not remove partially written evidence automatically.  Leaving it in
        # place makes the failure visible and prevents a silent overwrite.
        raise
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
